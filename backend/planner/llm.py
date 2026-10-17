"""
One small wrapper around the Claude API, used by every LLM part of the planner.

- Model: Claude Opus 5.5 (override with PLANNER_MODEL in .env).
- Key: ANTHROPIC_API_KEY from .env. No key (or PLANNER_LLM=off) = "no-key mode":
  get_llm() returns None and every caller has a plain-code fallback, so tests and
  the demo still run.
- Server-side fallback is on: if Claude declines a request, the API retries it on a
  fallback model inside the same call.
- Cost: every call adds its token cost to .cost_usd, so the benchmark can report it.
- Cache: calls made with cache_key=... are saved to disk, so the benchmark can be
  re-scored later without spending money again.

The LLM never approves a plan. Whatever it says goes through rules.py.
"""

import hashlib
import json
import os
from pathlib import Path

from backend import config  # noqa: F401  (loads .env)

MODEL = os.getenv("PLANNER_MODEL", "claude-opus-5-5")
# $ per million tokens (input, output). Claude Opus 5.5 list price.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}
CACHE_DIR = Path(os.getenv("PLANNER_LLM_CACHE", "data/planner/llm_cache"))
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLM:
    def __init__(self, model: str = MODEL, effort: str = "medium"):
        import anthropic          # imported here so no-key mode never needs the package
        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.cost_usd = 0.0
        self.calls = 0

    def _add_cost(self, usage) -> None:
        p_in, p_out = PRICES.get(self.model, (4.0, 20.0))
        tokens_in = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * 1.25 \
            + (getattr(usage, "cache_read_input_tokens", 0) or 0) * 0.1
        self.cost_usd += (tokens_in * p_in + (usage.output_tokens or 0) * p_out) / 1e6
        self.calls += 1

    def create(self, messages: list, system: str | None = None, tools: list | None = None,
               max_tokens: int = 16000, effort: str | None = None):
        """One Messages API call. Returns the full response (content blocks, stop_reason, usage)."""
        kwargs = dict(model=self.model, max_tokens=max_tokens, messages=messages,
                      output_config={"effort": effort or self.effort},
                      betas=[FALLBACK_BETA], fallbacks="default")
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        response = self.client.beta.messages.create(**kwargs)
        self._add_cost(response.usage)
        return response

    def text(self, prompt: str, system: str | None = None, cache_key: str | None = None,
             max_tokens: int = 16000, effort: str | None = None) -> str:
        """Ask once, get the text answer. With cache_key, the answer is saved and reused."""
        path = None
        if cache_key:
            h = hashlib.sha256(json.dumps([self.model, system, prompt, cache_key]).encode()).hexdigest()[:20]
            path = CACHE_DIR / f"{h}.json"
            if path.exists():
                return json.loads(path.read_text())["text"]
        r = self.create([{"role": "user", "content": prompt}], system=system, max_tokens=max_tokens, effort=effort)
        if r.stop_reason == "refusal":
            out = ""
        else:
            out = "".join(b.text for b in r.content if b.type == "text")
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"model": self.model, "cache_key": cache_key, "text": out}))
        return out


def llm_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY")) and os.getenv("PLANNER_LLM", "on").lower() != "off"


def get_llm(**kwargs) -> LLM | None:
    """The LLM, or None in no-key mode."""
    return LLM(**kwargs) if llm_available() else None


def cached_text(prompt: str, system: str | None, cache_key: str, model: str = MODEL) -> str | None:
    """Read a saved answer without needing a key (for re-scoring old benchmark runs)."""
    h = hashlib.sha256(json.dumps([model, system, prompt, cache_key]).encode()).hexdigest()[:20]
    path = CACHE_DIR / f"{h}.json"
    return json.loads(path.read_text())["text"] if path.exists() else None


def extract_json(text: str):
    """Pull the first JSON object out of an LLM answer (it may wrap it in ```json fences)."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None
