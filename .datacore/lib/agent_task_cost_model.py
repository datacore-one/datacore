#!/usr/bin/env python3
"""Agent-task unit economics model.

Prices an agent TASK end-to-end (multi-turn tool loop with prompt caching)
instead of a single API call. Created for org-96e0712e225d (Datafund strategy,
2026-06-11). Empirical anchor: ccusage over ~/.claude/projects (44 days,
272 sessions, $9,262.91 at API list prices, token mix 97.2% cache reads).

Usage:
    python3 agent_task_cost_model.py            # print scenario tables
    from agent_task_cost_model import task_cost # use as a library

Pricing source: claude-api skill, cached 2026-06-04 (USD per 1M tokens).
"""

from dataclasses import dataclass

# ---------------------------------------------------------------- pricing ---
# (input, output) USD per 1M tokens. Cache read = 0.1x input, write = 1.25x.
PRICES = {
    "opus-4.8":   (5.00, 25.00),
    "sonnet-4.6": (3.00, 15.00),
    "haiku-4.5":  (1.00, 5.00),
    "fable-5":    (10.00, 50.00),   # + ~1.3x tokenizer inflation, see below
    "open-weight": (0.50, 1.50),    # commodity hosted open-weight class (indicative)
}
CACHE_READ_MULT = 0.10
CACHE_WRITE_MULT = 1.25
TOKEN_INFLATION = {"fable-5": 1.30}  # new tokenizer ≈ +30% tokens same content


@dataclass
class TaskArchetype:
    name: str
    turns: int          # model invocations in the agent loop (incl. subagents)
    base_ctx: int       # initial context: system prompt + tools + task brief (tokens)
    growth: int         # tokens appended per turn (tool results + assistant turn)
    out_per_turn: int   # billed output tokens per turn (text + tool calls + thinking)
    ctx_cap: int = 160_000  # compaction cap — context stops growing here


ARCHETYPES = [
    TaskArchetype("Micro (triage/classify)",            3,   6_000, 1_500,  300),
    TaskArchetype("Simple (draft, single-file edit)",  15,  20_000, 4_000,  500),
    TaskArchetype("Standard (research brief, analysis)", 45,  30_000, 6_000,  800),
    TaskArchetype("Complex (feature build + tests)",   300,  40_000, 8_000, 1_200),
    TaskArchetype("Marathon (overnight autonomous run)", 800, 40_000, 8_000, 1_200),
]


def task_cost(a: TaskArchetype, model: str, cached: bool = True) -> dict:
    """Cost of one agent task. cached=False models a flat-price provider
    (or per-API-call mental model) that re-bills full context every turn."""
    p_in, p_out = PRICES[model]
    infl = TOKEN_INFLATION.get(model, 1.0)

    reads = writes = fresh = 0
    ctx = a.base_ctx
    for i in range(a.turns):
        if i == 0:
            writes += ctx                      # first turn writes the base context
        else:
            reads += ctx                       # re-read accumulated context
            writes += min(a.growth, max(0, a.ctx_cap - ctx))
        ctx = min(ctx + a.growth, a.ctx_cap)
    out = a.turns * a.out_per_turn

    reads, writes, out = (int(x * infl) for x in (reads, writes, out))
    if cached:
        c_in = (reads * CACHE_READ_MULT + writes * CACHE_WRITE_MULT) / 1e6 * p_in
    else:
        c_in = (reads + writes) / 1e6 * p_in   # every token at full input price
    c_out = out / 1e6 * p_out
    return {"input_cost": c_in, "output_cost": c_out, "total": c_in + c_out,
            "tokens": reads + writes + out}


def effective_cost(c: float, success_rate: float, review_minutes: float = 0,
                   human_rate_hr: float = 60.0) -> float:
    """Expected cost per SUCCESSFUL task: retries + human review overhead."""
    return c / success_rate + review_minutes / 60 * human_rate_hr


if __name__ == "__main__":
    print(f"{'Archetype':38} {'turns':>5} " +
          " ".join(f"{m:>11}" for m in PRICES) + f" {'flat-price*':>11}")
    for a in ARCHETYPES:
        row = [task_cost(a, m)["total"] for m in PRICES]
        flat = task_cost(a, "opus-4.8", cached=False)["total"]
        print(f"{a.name:38} {a.turns:>5} " +
              " ".join(f"${c:>10.2f}" for c in row) + f" ${flat:>10.2f}")
    print("\n* flat-price = same Opus workload billed without cache discounts")
    print("  (what a per-API-call model implicitly assumes)")

    print("\nBreak-even vs human labor (Opus 4.8, success-rate adjusted):")
    for a in ARCHETYPES:
        c = task_cost(a, "opus-4.8")["total"]
        for p in (0.9, 0.7):
            eff = effective_cost(c, p, review_minutes=5)
            print(f"  {a.name:38} p={p:.1f}  eff=${eff:7.2f}  "
                  f"breaks even vs {eff/60:5.2f}h of human work @ $60/h")
