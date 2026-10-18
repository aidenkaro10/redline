"""
LocalLLM: the planner's LLM, running on this laptop. Same interface as backend/planner/llm.py's LLM,
so the planner agent can use it without changes:

    .text(prompt, system=None, cache_key=None, max_tokens=..., effort=None) -> str
    .create(messages, system=None, tools=None, max_tokens=..., effort=None) -> response
    .cost_usd  (always 0.0: nothing leaves the laptop)    .calls    .model    .effort

Model: Qwen3-1.7B (4-bit, Apache 2.0) + our LoRA adapter (ml/checkpoints/local_planner/adapter).
No adapter on disk, or adapter=None = the plain base model.

Privacy: prompts (crew names, forecasts, heat numbers) never leave the machine. Works offline.

What it does NOT do:
  - Safety calls. Whatever it writes goes through the code rule checker, like Claude's answers.
  - Tool use. create(tools=...) answers in plain text only, and `supports_tools` is False, so the
    chat agent should use its no-key path (a summary of the verified plan) in local mode.
  - The LLM critic step by default (skip_critic=True returns no complaints; the code critic still
    runs). The teacher data was made the same way. Set PLANNER_LOCAL_CRITIC=1 to ask the model anyway.

Example:
    from ml.local_planner.client import LocalLLM
    from backend.planner.archon import plan_with_agent
    r = plan_with_agent(scenario, llm=LocalLLM())
"""

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ml.local_planner.common import ADAPTER_DIR, BASE_MODEL_DIR
from ml.local_planner.prompts import compact_prompt

LOCAL_MAX_TOKENS = int(os.getenv("PLANNER_LOCAL_MAX_TOKENS", "2500"))    # a plan is ~600-900 tokens
_CACHE = {}                    # (model dir, adapter dir) -> (model, tokenizer), loaded once per process
_LOCK = threading.Lock()       # one generation at a time (the GPU can't share a model across threads)


def load_model(model_dir: Path = BASE_MODEL_DIR, adapter_dir: Path | None = ADAPTER_DIR):
    """Load (once) the base model and, if given and present, the LoRA adapter."""
    import mlx.core as mx
    from mlx_lm import load
    # Cap mlx's buffer cache. Without it, long sampling runs pinned ~11 GB on this 16 GB Mac and it swapped.
    mx.set_cache_limit(int(os.getenv("LOCAL_PLANNER_MLX_CACHE_GB", "1")) << 30)
    adapter = str(adapter_dir) if adapter_dir and (Path(adapter_dir) / "adapters.safetensors").exists() else None
    key = (str(model_dir), adapter)
    if key not in _CACHE:
        _CACHE[key] = load(str(model_dir), adapter_path=adapter)
    return _CACHE[key], adapter


@dataclass
class _Text:                   # looks like the Anthropic SDK's text block
    text: str
    type: str = "text"


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class _Response:               # looks like the Anthropic SDK's message (the fields the planner reads)
    content: list
    stop_reason: str = "end_turn"
    usage: _Usage = field(default_factory=_Usage)


class LocalLLM:
    supports_tools = False

    def __init__(self, model_dir: Path = BASE_MODEL_DIR, adapter_dir: Path | None = ADAPTER_DIR,
                 temperature: float = 0.0, seed: int = 0, skip_critic: bool | None = None, effort: str = "medium"):
        (self.model_obj, self.tokenizer), self.adapter = load_model(model_dir, adapter_dir)
        self.model = f"local:{Path(model_dir).name}" + ("+lora" if self.adapter else "")
        self.effort = effort                       # kept for interface parity; a local model has no effort knob
        self.temperature = temperature
        self.seed = seed
        self.skip_critic = (os.getenv("PLANNER_LOCAL_CRITIC", "0") != "1") if skip_critic is None else skip_critic
        self.cost_usd = 0.0
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.seconds = 0.0
        self.log = []                              # (prompt, answer) of every text() call, for fact checks

    def _generate(self, messages: list, max_tokens: int) -> tuple[str, int, int]:
        import mlx.core as mx
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        prompt = self.tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False,
                                                    enable_thinking=False)   # answer directly, no <think>
        sampler = make_sampler(temp=self.temperature, top_p=0.95 if self.temperature > 0 else 0.0)
        text, n_in, n_out = "", 0, 0
        with _LOCK:
            mx.random.seed(self.seed + self.calls)
            for r in stream_generate(self.model_obj, self.tokenizer, prompt, max_tokens=min(max_tokens, LOCAL_MAX_TOKENS),
                                     sampler=sampler):
                text += r.text
                n_in, n_out = r.prompt_tokens, r.generation_tokens
        return text, n_in, n_out

    def text(self, prompt: str, system: str | None = None, cache_key: str | None = None,
             max_tokens: int = 16000, effort: str | None = None) -> str:
        """Ask once, get the text answer (same signature as the Claude wrapper; cache_key is ignored)."""
        if self.skip_critic and "Verified plans:" in prompt and "List concrete complaints" in prompt:
            return '{"complaints": []}'
        prompt = compact_prompt(prompt)            # same shortening the training data got
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        t0 = time.time()
        out, n_in, n_out = self._generate(msgs, max_tokens)
        self.calls += 1
        self.tokens_in += n_in
        self.tokens_out += n_out
        self.seconds += time.time() - t0
        self.log.append({"prompt": prompt, "answer": out})
        return out

    def create(self, messages: list, system: str | None = None, tools: list | None = None,
               max_tokens: int = 16000, effort: str | None = None) -> _Response:
        """Plain-text reply to a conversation. Tools are NOT supported (see supports_tools)."""
        msgs = [{"role": "system", "content": system}] if system else []
        for m in messages:
            c = m["content"]
            if not isinstance(c, str):            # SDK blocks / tool results -> their text only
                c = "\n".join(getattr(b, "text", None) or (b.get("text") or b.get("content") or "")
                              if isinstance(b, dict) else (getattr(b, "text", "") or "") for b in c)
            msgs.append({"role": m["role"], "content": str(c)})
        out, n_in, n_out = self._generate(msgs, max_tokens)
        self.calls += 1
        return _Response(content=[_Text(out)], usage=_Usage(n_in, n_out))
