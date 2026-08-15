"""base.py -- Executor contract, registry, and live shadow accounting.

`Executor` is the base class every model/harness adapter subclasses. The
ONLY method a real adapter needs to implement is `_invoke(prompt, timeout_s)
-> (text, cost_cents)` -- that is also the injection point tests use: a
FakeTransport is just a plain function swapped in via
`monkeypatch.setattr(executor, "_invoke", fake_fn)`, so the conformance
suite in tests/test_executors.py exercises every adapter's `run()` semantics
without touching a network or subprocess.

`run()` here owns everything adapters must not have to reimplement:

- **Never-raise wrapping.** Exception subclasses `_invoke` raises (including
  `subprocess.TimeoutExpired`, mapped to a clearer message) never propagate
  out of `run()` -- each becomes `ExecResult.error` instead. (Precisely:
  anything `except Exception` catches; `BaseException` siblings such as
  `KeyboardInterrupt`/`SystemExit` are deliberately not caught here and are
  not the concern of this guarantee.)
- **Schema-contract prompting.** When `schema` is given, a JSON-contract
  instruction is appended to the prompt before `_invoke` sees it, and the
  response text is parsed as JSON afterward. Parse failure is NOT an error
  (a schema violation is a normal, expected outcome for many callers, not
  an execution failure) -- it sets `parsed=None` and `parse_ok=False`,
  leaving `ExecResult.error` untouched. `parse_ok` is `None` when no schema
  was requested at all, so callers can distinguish "didn't ask" from
  "asked and it parsed/didn't".
- **Spend emission -- shadow accounting, live.** EVERY successful `_invoke`
  (i.e. every run that didn't raise) emits a `spend.record` ledger event
  via `ledger.log.EventLog`, unless `DATACORE_NO_SPEND=1`. Actor and space
  resolution mirror `ledger_cli.py` / `job_verify.py`: `$DATACORE_ACTOR`
  else hostname; `$DATACORE_ROOT` else `~/Data`. Signing is left at
  `EventLog`'s own default (opt-in via `$DATACORE_LEDGER_SIGN=1`), so a
  bare run stays unsigned.
- **Cost floor -- conservation invariant.** `fold()` (see `ledger/fold.py`
  `_handle_spend`) sums `cost_cents` onto the actor's ledger balance
  naively -- a negative or unparseable `cost_cents` from a malformed or
  buggy `_invoke` must never be allowed to silently DECREMENT that
  balance. `run()` coerces `cost_cents` to `max(0, int(cost_cents))`
  before it is used for either the emitted event or the returned
  `ExecResult`; a value that can't be coerced to a non-negative int is
  clamped to `0` and the emitted ref gets a `:clamped` suffix so the
  anomaly is auditable rather than silent.
- **In-band adapter errors still spend.** An adapter can flag that the
  transport call actually ran and consumed real cost, but the CONTENT it
  got back represents a reported failure (e.g. claude-code's JSON envelope
  carrying `is_error: true`) -- by setting `self._in_band_error =
  "<message>"` inside `_invoke` (mirroring the `self._cost_estimated`
  signaling pattern below, since `_invoke`'s return shape stays a plain
  `(text, cost_cents)` tuple). `run()` surfaces that message as
  `ExecResult.error`, but -- unlike a raised exception -- STILL emits the
  spend event, with the ref marked `:err`, because real cost was actually
  incurred.
- **Accounting hiccups never break the run.** If the spend emission itself
  raises (disk full, unwritable space, whatever), the run's `text` /
  `parsed` / `cost_cents` -- the actual result of doing the work -- must
  still come back intact. Two cases: if `text` is empty (there is nothing
  else useful to report), `error` becomes
  `"spend emission failed: <exc>"`; if `text` is non-empty (the run
  produced real output), `error` is set to the short marker
  `"[spend-emit-failed]"` rather than a full message that would read as
  "the run failed" -- the accounting hiccup is surfaced, but it must never
  read as if the underlying work failed when it didn't. (If an in-band
  error message was already set, the emission note is appended to it
  rather than overwriting it.)
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ledger.log import EventLog

# Shadow-accounting estimate rate: used ONLY when an adapter has no real
# usage/cost figure to parse from its transport. This is a rough ledger
# placeholder for cost tracking, NOT a real Anthropic billing rate -- do
# not treat cost_cents produced via this path as an actual invoice figure.
ESTIMATE_CENTS_PER_MILLION_TOKENS = 300
ESTIMATE_CHARS_PER_TOKEN = 4


def estimate_cost_cents(prompt: str, text: str) -> int:
    """Rough cost estimate for adapters with no real usage/cost fields to
    parse: `(len(prompt) + len(text)) / ESTIMATE_CHARS_PER_TOKEN` chars-per-
    token, priced at `ESTIMATE_CENTS_PER_MILLION_TOKENS` cents per 1,000,000
    tokens. Callers that use this estimate MUST also set
    `self._cost_estimated = True` inside `_invoke` so `run()` marks the
    emitted spend ref with the `:est` suffix (see `Executor.run`)."""
    tokens = (len(prompt) + len(text)) / ESTIMATE_CHARS_PER_TOKEN
    return round(tokens * ESTIMATE_CENTS_PER_MILLION_TOKENS / 1_000_000)


@dataclass
class ExecResult:
    text: str
    parsed: dict | None
    cost_cents: int
    error: str | None
    # None = no schema was requested; True/False = schema parse outcome.
    # A separate field rather than overloading `error` -- a parse failure
    # is not an execution failure and must not read as one.
    parse_ok: bool | None = None
    # Which model actually served the request, as the transport reported it --
    # NOT what was configured. Those differ whenever a fallback fires, and the
    # configured value would then record a model that never ran. None means the
    # adapter could not determine it, which is honest; a guess would not be.
    model: str | None = None


_REGISTRY: dict[str, type["Executor"]] = {}


def register(cls: type["Executor"]) -> type["Executor"]:
    """Class decorator: register `cls` under its `name` attribute so
    `get_executor()` can resolve it by string."""
    _REGISTRY[cls.name] = cls
    return cls


def registered_executors() -> dict[str, type["Executor"]]:
    """A copy of the registry -- name -> Executor subclass. Used by the
    conformance test suite to parametrize over every registered adapter
    without reaching into a private module attribute."""
    return dict(_REGISTRY)


def get_executor(name: str | None = None) -> "Executor":
    """Resolve an `Executor` instance by name.

    Resolution order: explicit `name` argument, else `$DATACORE_EXECUTOR`,
    else `"claude-code"`. An unrecognized name raises `ValueError` listing
    every registered name, so a typo'd config value fails loudly instead of
    silently picking a default.
    """
    resolved = name or os.environ.get("DATACORE_EXECUTOR") or "claude-code"
    try:
        cls = _REGISTRY[resolved]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY))
        raise ValueError(f"unknown executor {resolved!r} (expected one of: {known})") from None
    return cls()


def _default_actor() -> str:
    """Actor resolution mirrors `ledger_cli.py` / `job_verify.py`:
    `$DATACORE_ACTOR`, else `socket.gethostname()`."""
    return os.environ.get("DATACORE_ACTOR") or socket.gethostname()


def _default_space_dir() -> Path:
    """Space-dir resolution mirrors `ledger/keys.py` / `job_verify.py`'s
    `DATACORE_ROOT` constant, but read at CALL time rather than cached as a
    module-level constant -- executors run inside a long-lived process and
    tests need to monkeypatch `DATACORE_ROOT` per-test, which a value
    frozen at import time would never see."""
    return Path(os.environ.get("DATACORE_ROOT") or (Path.home() / "Data"))


def _schema_contract_instruction(schema: dict) -> str:
    return (
        "\n\nRespond with ONLY valid JSON matching this schema -- no prose, "
        "no markdown code fences, no explanation before or after the JSON:\n"
        + json.dumps(schema, sort_keys=True)
    )


class Executor:
    """Base class for model/harness adapters.

    Subclasses set a class-level `name` and override `_invoke` -- see the
    module docstring for the full contract `run()` provides on top of it.
    """

    name: str

    def __init__(self) -> None:
        # Set by `_invoke` implementations that had to estimate cost rather
        # than read a real usage/cost figure from their transport; reset by
        # `run()` before every call so a stale True from a prior call can
        # never leak into the next one's ref.
        self._cost_estimated = False
        # Set by `_invoke` implementations that detect an in-band content
        # error (transport succeeded, cost was incurred, but the response
        # itself reports failure) -- see the module docstring. Reset by
        # `run()` before every call for the same stale-state reason as
        # `_cost_estimated` above.
        self._in_band_error: str | None = None
        self._cwd = None
        self._model: str | None = None

    def _invoke(self, prompt: str, timeout_s: int) -> tuple[str, int]:
        """Real transport call. MUST be overridden by subclasses. Returns
        `(text, cost_cents)`. Free to raise -- `run()` never lets an
        exception from here escape; see the module docstring."""
        raise NotImplementedError

    def run(self, prompt: str, *, schema: dict | None = None, timeout_s: int = 300,
            cwd=None, space=None, item: str | None = None) -> ExecResult:
        """Run the executor. Never raises -- every failure mode becomes
        `ExecResult.error` instead. See the module docstring for the full
        contract (schema handling, spend emission, timeout mapping).

        `cwd` is the directory the agent should work in. It is part of the
        INTERFACE rather than an environment variable because it was lost once
        already: the dispatcher used to spawn agents with `cwd=<space>`, the
        move to this registry dropped it silently, and nothing could notice
        because no signature mentioned it. An adapter that does not spawn a
        process ignores it; one that does reads `self._cwd`.
        """
        self._cwd = cwd
        effective_prompt = prompt
        if schema is not None:
            try:
                effective_prompt = prompt + _schema_contract_instruction(schema)
            except (TypeError, ValueError) as exc:
                return ExecResult(text="", parsed=None, cost_cents=0, error=f"invalid schema: {exc}", parse_ok=None)

        self._cost_estimated = False
        self._in_band_error = None
        self._model = None
        try:
            text, cost_cents = self._invoke(effective_prompt, timeout_s)
        except subprocess.TimeoutExpired as exc:
            return ExecResult(
                text="",
                parsed=None,
                cost_cents=0,
                error=f"executor {self.name!r} timed out after {timeout_s}s: {exc}",
                parse_ok=None,
                model=self._model,
            )
        except Exception as exc:  # noqa: BLE001 -- adapters must never raise out of run()
            return ExecResult(
                text="",
                parsed=None,
                cost_cents=0,
                model=self._model,
                error=f"executor {self.name!r} failed: {exc}",
                parse_ok=None,
            )

        # Conservation-invariant cost floor: never let a negative or
        # unparseable cost_cents from a malformed/buggy _invoke reach the
        # ledger (fold() sums naively -- see module docstring). Applies to
        # BOTH the emitted event and the returned ExecResult, since they
        # share this one normalized value.
        cost_clamped = False
        try:
            normalized_cost_cents = int(cost_cents)
        except Exception:  # noqa: BLE001 -- e.g. TypeError/ValueError/OverflowError (float('inf')): any of
            # these just means "couldn't normalize", never something run() should propagate.
            normalized_cost_cents = None
        if normalized_cost_cents is None or normalized_cost_cents < 0:
            cost_cents = 0
            cost_clamped = True
        else:
            cost_cents = normalized_cost_cents

        parsed: dict | None = None
        parse_ok: bool | None = None
        if schema is not None:
            try:
                parsed = json.loads(text)
                parse_ok = True
            except Exception:  # noqa: BLE001 -- any parse failure just means "didn't parse"
                parsed, parse_ok = None, False

        # An in-band content error (see module docstring) surfaces as
        # ExecResult.error, but -- unlike a raised exception -- does NOT
        # skip spend emission below: the transport call actually ran and
        # consumed real cost.
        error: str | None = self._in_band_error

        if os.environ.get("DATACORE_NO_SPEND") != "1":
            try:
                # WRITE SPEND WHERE SOMEONE WILL FOLD IT, ATTRIBUTED TO THE
                # DECLARED ACTOR, AND LINKED TO THE ITEM THAT INCURRED IT.
                #
                # All three were wrong. `_default_space_dir()` returns
                # DATACORE_ROOT -- `~/Data` -- which is NOT a space, so every
                # spend event went into an orphan log no fold ever reads. The
                # actor came from `socket.gethostname()`, producing files like
                # `Mac.jsonl` and `air-23.local.jsonl` instead of the declared
                # DIP-0044 actors. And the payload named only the adapter, so
                # "what did this delegation cost" was unanswerable even in
                # principle.
                #
                # A forensic pass over a 15-item run found no spend events in
                # any space and concluded the type did not exist. It did; it
                # was being filed somewhere nobody looks.
                #
                # The caller knows the space and the item. Both are optional so
                # non-dispatch callers keep working unchanged.
                space_dir = Path(space) if space else _default_space_dir()
                actor = os.environ.get("DATACORE_ACTOR") or _default_actor()
                log = EventLog(space_dir, actor)
                ref = f"executor:{self.name}"
                if self._cost_estimated:
                    ref += ":est"
                if cost_clamped:
                    ref += ":clamped"
                if self._in_band_error is not None:
                    ref += ":err"
                payload = {"cents": cost_cents, "ref": ref, "executor": self.name}
                if item:
                    payload["item"] = item
                if self._model:
                    payload["model"] = self._model
                log.append("spend.record", payload)
            except Exception as exc:  # noqa: BLE001 -- accounting hiccups must not break the run
                if not text:
                    emission_note = f"spend emission failed: {exc}"
                else:
                    emission_note = "[spend-emit-failed]"
                error = f"{error} {emission_note}" if error else emission_note

        return ExecResult(text=text, parsed=parsed, cost_cents=cost_cents,
                          error=error, parse_ok=parse_ok, model=self._model)
