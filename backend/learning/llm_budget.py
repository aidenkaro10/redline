"""
Claude with a hard spending cap, for the learning loops.

BudgetLLM looks like the planner's LLM (backend/planner/llm.py) from the outside:
    .text(prompt, system=..., effort=...)  -> str
    .cost_usd                              -> dollars spent so far
so it can be handed straight to the planner agent (plan_with_agent(..., llm=...)).

What it adds:
  1. A hard cap. Before every call it adds up the worst case (prompt tokens + the
     max output tokens). If that would go over the cap, it doesn't call Claude.
     It raises BudgetExceeded, or returns "" if the caller asked it to fail soft
     (the planner then just runs without the LLM step, which is always safe).
  2. A cost log. Every call writes one line to data/learning/cost_log.jsonl:
     which loop, what for, tokens in/out, dollars, running total.
  3. API errors never crash a loop. They're logged and the call returns "".

No key = get_budget_llm() returns None and every loop uses its plain-code fallback.
Tests use FakeLLM (fake_llm.py), which never touches the network.
"""

import json
import threading
import time

from backend.learning import settings
from backend.planner.llm import MODEL, PRICES, llm_available


class BudgetExceeded(RuntimeError):
    pass


def _tokens_estimate(text: str) -> int:
    """Rough token count (about 3.5 characters per token). Used only for the worst-case check."""
    return int(len(text) / 3.5) + 50


class BudgetLLM:
    def __init__(self, cap_usd: float, loop: str, model: str = MODEL, effort: str = "medium",
                 fail_soft: bool = True, client=None):
        if client is None:
            from backend.planner.llm import LLM      # imported here so no-key mode never needs anthropic
            client = LLM(model=model, effort=effort)
        self.client = client                         # the planner's LLM wrapper (does the real API call)
        self.model = model
        self.cap_usd = cap_usd
        self.loop = loop
        self.fail_soft = fail_soft
        self.cost_usd = 0.0
        self.calls = 0
        self.blocked = 0                             # calls skipped because of the cap
        self.errors = 0
        self.purpose = ""                            # set by the loop before a call, for the log
        self.reserved = 0.0                          # worst-case cost of calls in flight (parallel threads)
        self._lock = threading.Lock()

    def _price(self, tokens_in: float, tokens_out: float) -> float:
        p_in, p_out = PRICES.get(self.model, (4.0, 20.0))
        return (tokens_in * p_in + tokens_out * p_out) / 1e6

    def remaining(self) -> float:
        return max(0.0, self.cap_usd - self.cost_usd)

    def text(self, prompt: str, system: str | None = None, cache_key: str | None = None,
             max_tokens: int | None = None, effort: str | None = None) -> str:
        max_tokens = min(max_tokens or settings.LLM_MAX_TOKENS, settings.LLM_MAX_TOKENS)
        worst = self._price(_tokens_estimate(prompt + (system or "")), max_tokens)
        purpose = self.purpose
        with self._lock:                             # reserve the worst case, so parallel calls can't overshoot
            over = self.cost_usd + self.reserved + worst > self.cap_usd
            if over:
                self.blocked += 1
            else:
                self.reserved += worst
        if over:
            self._log({"event": "blocked_by_budget", "worst_case_usd": round(worst, 4)}, purpose)
            if self.fail_soft:
                return ""
            raise BudgetExceeded(f"{self.loop}: next call could cost ${worst:.3f}, "
                                 f"only ${self.remaining():.3f} left of ${self.cap_usd:.2f}")
        t0 = time.time()
        try:
            r = self.client.create([{"role": "user", "content": prompt}], system=system,
                                   max_tokens=max_tokens, effort=effort)
        except Exception as e:                       # network, rate limit, bad request: log it, carry on
            with self._lock:
                self.reserved -= worst
                self.errors += 1
            self._log({"event": "error", "error": f"{type(e).__name__}: {str(e)[:200]}"}, purpose)
            return ""
        u = r.usage
        tokens_in = (u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) * 1.25 \
            + (getattr(u, "cache_read_input_tokens", 0) or 0) * 0.1
        cost = self._price(tokens_in, u.output_tokens or 0)
        with self._lock:
            self.reserved -= worst
            self.cost_usd += cost
            self.calls += 1
        out = "" if r.stop_reason == "refusal" else "".join(b.text for b in r.content if b.type == "text")
        self._log({"event": "call", "tokens_in": int(tokens_in), "tokens_out": u.output_tokens,
                   "cost_usd": round(cost, 5), "stop_reason": r.stop_reason, "latency_s": round(time.time() - t0, 1)},
                  purpose)
        return out

    def _log(self, row: dict, purpose: str = "") -> None:
        settings.COST_LOG.parent.mkdir(parents=True, exist_ok=True)
        row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "loop": self.loop, "purpose": purpose,
               "model": self.model, **row, "total_usd": round(self.cost_usd, 5), "cap_usd": self.cap_usd}
        with self._lock, settings.COST_LOG.open("a") as f:
            f.write(json.dumps(row) + "\n")

    def summary(self) -> dict:
        return {"model": self.model, "calls": self.calls, "cost_usd": round(self.cost_usd, 4),
                "cap_usd": self.cap_usd, "blocked_by_cap": self.blocked, "errors": self.errors}


def get_budget_llm(cap_usd: float, loop: str, **kwargs) -> BudgetLLM | None:
    """Claude with a cap, or None in no-key mode (or when PLANNER_LLM=off)."""
    return BudgetLLM(cap_usd, loop, **kwargs) if llm_available() else None


def llm_summary(llm) -> dict:
    """Cost summary for any LLM-like object (BudgetLLM, FakeLLM or None)."""
    if llm is None:
        return {"model": None, "calls": 0, "cost_usd": 0.0}
    if hasattr(llm, "summary"):
        return llm.summary()
    return {"model": getattr(llm, "model", "fake"), "calls": getattr(llm, "calls", 0),
            "cost_usd": round(getattr(llm, "cost_usd", 0.0), 4)}
