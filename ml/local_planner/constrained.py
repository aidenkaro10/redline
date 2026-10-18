"""
Constrained decoding: the model can only write schedules with the right shape and legal ids.

The plan JSON has a fixed skeleton:
    {"assignments":{"w1":["t3","rest",...],"w2":[...],...},"notes":"<free text>"}
Code knows every worker id, the number of slots and which values are legal in each slot. So at
every step we only let the model pick tokens that keep the text on that skeleton. The model still
decides WHAT goes in each slot; it just can't write a broken schedule. After the skeleton, the
notes are free text.

Two levels of "legal":
  format   right shape; the worker is on site; has the task's skill; the task is in its time window
  rules    format + the simple hard rules that need no heat model: no outdoor work in stop-work
           windows (storms, lightning + all-clear), no very heavy work for new hires in high heat,
           and the break rule (max work slots in a row)
Heat (90th pct core temp < 38.0 °C) is NOT enforced here: it needs the full heat model. The rule
checker still checks every plan afterwards, as always.

No new libraries: it's a logits processor for mlx-lm (sets every illegal token's logit to -inf).
"""

import numpy as np

from backend import config
from backend.planner.scenario import REST, Scenario

CHARSET = set('{}[]":,_') | set("abcdefghijklmnopqrstuvwxyz0123456789")
_VOCAB_CACHE = {}


def vocab_map(tokenizer) -> dict[str, int]:
    """
    text -> token id, for every token made only of JSON-skeleton characters (built once).
    Byte-level BPE stores printable ASCII (except space) as itself, so for these characters the
    vocab string IS the decoded text. That's much faster than decoding 151k tokens one by one.
    """
    key = id(tokenizer)
    if key not in _VOCAB_CACHE:
        hf = getattr(tokenizer, "_tokenizer", tokenizer)          # mlx-lm wraps the HF tokenizer
        m = {}
        for s, i in sorted(hf.get_vocab().items(), key=lambda kv: kv[1]):
            if s and set(s) <= CHARSET and s not in m:
                m[s] = i
        _VOCAB_CACHE[key] = m
    return _VOCAB_CACHE[key]


class PlanGrammar:
    """
    Tracks where we are in the skeleton and which next pieces of text are legal.
    Items: one per (worker, slot): a fixed literal (like ',' or '],"w2":[') then a choice
    among quoted values ('"t3"', '"rest"'). After the last item comes the tail literal
    ']},"notes":"' and then free text.
    """

    def __init__(self, scenario: Scenario, level: str = "format"):
        assert level in ("format", "rules")
        self.sc, self.level = scenario, level
        self.workers = [w.id for w in scenario.crew]
        self.n = scenario.n_slots
        self.items = []                                   # (worker index, slot, literal before the choice)
        for i, wid in enumerate(self.workers):
            for k in range(self.n):
                if k == 0:
                    lit = ('{"assignments":{' if i == 0 else '],') + f'"{wid}":['
                else:
                    lit = ","
                self.items.append((i, k, lit))
        self.tail = ']},"notes":"'
        self.chosen = {wid: [] for wid in self.workers}
        self.j = 0                                        # current item
        self.partial = ""                                 # text written for the current item so far
        self.free = False                                 # past the skeleton: notes are free text
        # precomputed rule facts
        self.blocked = scenario.stop_work_slots()
        self.hot = [w.wbgt >= config.PLAN_HOT_WBGT_C for w in scenario.weather]
        self.cycled = scenario.cycled_slots() if hasattr(scenario, "cycled_slots") else set()

    # -- what's legal -----------------------------------------------------------
    def legal_values(self, i: int, k: int) -> list[str]:
        w = self.sc.crew[i]
        vals = [REST]
        if not w.is_available(k):
            return vals
        for t in self.sc.tasks:
            deadline = t.deadline if t.deadline is not None else self.n
            if (t.skill and t.skill not in w.skills) or not (t.earliest <= k < deadline):
                continue
            if self.level == "rules":
                if t.outdoor and k in self.blocked:
                    continue
                if (t.intensity == "very_heavy" and w.acclim < config.PLAN_UNACCLIM_LEVEL
                        and self.sc.weather[k].wbgt >= config.PLAN_UNACCLIM_NO_VERY_HEAVY_WBGT_C):
                    continue
                if not self._break_ok(i, k):
                    continue
            vals.append(t.id)
        return vals

    def _break_ok(self, i: int, k: int) -> bool:
        """Would one more work slot at k make a run longer than the break rule allows?"""
        if k in self.cycled:
            return True                                   # cycled slots have their break built in
        w = self.sc.crew[i]
        prev = self.chosen[self.workers[i]]
        run = 0
        for s in range(k - 1, -1, -1):
            a = prev[s]
            if a == REST or not w.is_available(s) or s in self.cycled:
                break
            run += 1
        new_len = run + 1
        if new_len > config.PLAN_MAX_CONSECUTIVE_WORK_SLOTS:
            return False
        if new_len > config.PLAN_MAX_CONSECUTIVE_WORK_SLOTS_HOT and any(self.hot[k - run:k + 1]):
            return False
        return True

    def options(self) -> list[str]:
        """Every legal continuation of the text, up to the end of the NEXT literal."""
        if self.free:
            return []
        if self.j >= len(self.items):                    # only the tail is left
            return [self.tail[len(self.partial):]] if self.tail.startswith(self.partial) else []
        i, k, lit = self.items[self.j]
        nxt = self.items[self.j + 1][2] if self.j + 1 < len(self.items) else self.tail
        out = []
        for v in self.legal_values(i, k):
            full = f'{lit}"{v}"{nxt}'
            if full.startswith(self.partial):
                out.append(full[len(self.partial):])
        return out

    # -- moving forward -----------------------------------------------------------
    def advance(self, text: str) -> None:
        """Feed the text of the token the model just picked."""
        for ch in text:
            if self.free:
                return
            self.partial += ch
            if self.j >= len(self.items):
                if self.partial == self.tail:
                    self.free = True
                continue
            i, k, lit = self.items[self.j]
            if len(self.partial) > len(lit) + 1 and self.partial.endswith('"') and self.partial.startswith(lit + '"'):
                value = self.partial[len(lit) + 1:-1]
                self.chosen[self.workers[i]].append(value)
                self.j += 1
                self.partial = ""

    def assignments(self) -> dict | None:
        return self.chosen if self.j >= len(self.items) else None


class GrammarProcessor:
    """mlx-lm logits processor: everything the grammar doesn't allow gets -inf."""

    def __init__(self, grammar: PlanGrammar, tokenizer, vmap: dict[str, int]):
        self.g, self.tok, self.vmap = grammar, tokenizer, vmap
        self.start = None                                 # how many tokens existed before generation
        self.fed = 0

    def __call__(self, tokens, logits):
        import mlx.core as mx
        n = int(tokens.shape[-1]) if tokens is not None else 0
        if self.start is None:
            self.start = n
        new = tokens.tolist()[self.start + self.fed:] if n > self.start + self.fed else []
        for t in new:                                     # catch up on what the model just wrote
            self.g.advance(self.tok.decode([t]))
            self.fed += 1
        if self.g.free:
            return logits
        allowed = set()
        for opt in self.g.options():
            for L in range(1, len(opt) + 1):
                tid = self.vmap.get(opt[:L])
                if tid is not None:
                    allowed.add(tid)
        mask = np.full(logits.shape[-1], -np.inf, dtype=np.float32)
        mask[list(allowed)] = 0.0
        return logits + mx.array(mask)


def generate_constrained(model, tokenizer, scenario: Scenario, prompt_ids: list[int], k: int,
                         temperature: float, level: str = "format", max_tokens: int = 1200):
    """k constrained samples in one batch. Returns [(text, assignments or None)]."""
    from mlx_lm.generate import BatchGenerator
    from mlx_lm.sample_utils import make_sampler
    vmap = vocab_map(tokenizer)
    grammars = [PlanGrammar(scenario, level) for _ in range(k)]
    gen = BatchGenerator(model, stop_tokens=[[t] for t in tokenizer.eos_token_ids],
                         sampler=make_sampler(temp=temperature, top_p=0.95 if temperature > 0 else 0.0))
    uids = gen.insert([prompt_ids] * k, [max_tokens] * k,
                      logits_processors=[[GrammarProcessor(g, tokenizer, vmap)] for g in grammars])
    out = {u: [] for u in uids}
    while responses := gen.next_generated():
        for r in responses:
            if r.finish_reason != "stop":
                out[r.uid].append(r.token)
    gen.close()
    import mlx.core as mx
    mx.clear_cache()                                  # give the GPU memory back
    results = []
    for u, g in zip(uids, grammars):
        text = tokenizer.decode(out[u])
        results.append((text, g.assignments()))
    return results
