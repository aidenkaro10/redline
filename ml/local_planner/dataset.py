"""
Turn the teacher runs into chat training examples, STaR style: keep only what's verified and good.

For every saved teacher run (one training scenario) we make up to three examples, one per job
the LLM does in the planner:

  plan     B's "write the whole schedule" prompt (the same prompt Claude gets in llm_only)
           -> {"assignments": ..., "notes": ...}
           Target = the best plan that PASSED the rule checker:
             - Claude's own direct schedule, if it passed and scored as well as the agent's pick
               (STaR: keep the model's own correct answers), otherwise
             - the agent's final verified plan (STaR "rationalization": show the right answer).
  ideas    the agent's "strategy ideas" prompt (plan view made compact, see prompts.py) -> Claude's ideas (schema-checked), with the
           direct_plan replaced by the verified target plan (Claude's own direct plans fail
           the heat rule most of the time, we never teach a failing plan)
  explain  the agent's "explain to the crew lead" prompt -> Claude's bullets

A run is dropped if its final plan isn't verified, is the everyone-rests fallback, or does less
than MIN_THROUGHPUT of the work. Train/val is split by SCENARIO (all examples of a day go to the
same side), never by example.

Run:  python -m ml.local_planner.dataset
Out:  data/local_planner/sft/{train,valid}.jsonl  + stats.json
"""

import json
import re
from collections import Counter

from ml.local_planner.common import DATA_DIR, out_dir
from ml.local_planner.prompts import compact_prompt

from backend.planner.archon import IDEAS_SYSTEM
from backend.planner.baselines import LLM_SYSTEM, llm_prompt
from backend.planner.generate import assert_not_test
from backend.planner.llm import extract_json
from backend.planner.scenario import INTENSITIES, Scenario

RUNS_DIR = DATA_DIR / "teacher_runs"
SFT_DIR = DATA_DIR / "sft"
MIN_THROUGHPUT = 0.3          # a "good" plan gets at least 30% of the priority-weighted work done
RANK_TOL = 0.02               # Claude's direct plan counts as "as good" within this much rank
MAX_NOTE_CHARS = 280


def compact(obj) -> str:
    """JSON with no spaces: fewer tokens for the small model to read and write."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def first_sentence(text: str) -> str:
    text = re.sub(r"\*\*|__|`", "", text).strip()
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return (m.group(1) if m else text).strip()


def notes_from_reasons(reasons: list[str]) -> str:
    """A short plain explanation: the first sentence of each of Claude's first two bullets."""
    llm_bullets = [r for r in reasons if not r.startswith("Peak 90th pct")][:2]
    note = " ".join(first_sentence(r) for r in llm_bullets)
    if len(note) > MAX_NOTE_CHARS:
        note = note[:MAX_NOTE_CHARS].rsplit(" ", 1)[0] + "..."
    return note


def pick_target(run: dict) -> tuple[dict | None, str]:
    """The verified plan we teach for this scenario, and where it came from."""
    final, fs = run["final"], run.get("final_score") or {}
    if not final["ok"] or final["source"].startswith("fallback") or fs.get("throughput", 0) < MIN_THROUGHPUT:
        return None, "dropped: final plan not good enough"
    opt_ranks = [c["score"].get("rank", -9) for c in run["candidates"] if c["name"].startswith("optimizer:") and c["ok"]]
    if opt_ranks and fs.get("rank", -9) < max(opt_ranks) - 1e-6:
        return None, "dropped: final plan ranks below an optimizer-only plan"
    direct = next((c for c in run["candidates"] if c["name"] == "llm:direct"), None)
    if direct and direct["ok"] and direct["score"].get("rank", -9) >= fs["rank"] - RANK_TOL:
        return direct["assignments"], "claude_direct"
    return final["assignments"], "agent_final"


def clean_ideas(response: str, sc: Scenario, target: dict) -> dict | None:
    """Claude's ideas, keeping only fields/values the agent would accept, plus the verified direct plan."""
    data = extract_json(response or "")
    if not isinstance(data, dict):
        return None
    workers, tasks = {w.id for w in sc.crew}, {t.id for t in sc.tasks}
    ideas = []
    for idea in data.get("ideas") or []:
        if not isinstance(idea, dict):
            continue
        soft = [{"worker": p["worker"], "intensities": p.get("intensities", []), "from_slot": p.get("from_slot", 0),
                 "to_slot": p.get("to_slot", sc.n_slots), "penalty": int(p.get("penalty", 0))}
                for p in idea.get("soft", []) or []
                if isinstance(p, dict) and p.get("worker") in workers
                and set(p.get("intensities", [])) <= set(INTENSITIES) and 0 < int(p.get("penalty", 0)) <= 50]
        forbid = [f for f in idea.get("forbid", []) or [] if isinstance(f, list) and len(f) == 3
                  and f[0] in workers and f[2] in tasks and (f[1] is None or isinstance(f[1], int))]
        ideas.append({"name": str(idea.get("name", "idea"))[:60], "why": str(idea.get("why", ""))[:300],
                      "objective": idea.get("objective") if idea.get("objective") in
                      ("throughput", "balanced", "margin", "fairness") else "balanced",
                      "soft": soft, "forbid": forbid})
    if not ideas:
        return None
    return {"ideas": ideas, "direct_plan": {"assignments": target}}


def examples_from_run(run: dict) -> tuple[list[dict], str]:
    """All chat examples from one teacher run, and the plan target's source (for the stats)."""
    sc = Scenario.from_dict(run["scenario"])
    assert_not_test(sc)                                     # belt and braces: never learn from test-*
    target, source = pick_target(run)
    if target is None:
        return [], source
    out = []
    plan_answer = {"assignments": target, "notes": notes_from_reasons(run["final"]["reasons"])}
    out.append({"kind": "plan", "messages": [
        {"role": "system", "content": LLM_SYSTEM},
        {"role": "user", "content": llm_prompt(sc)},
        {"role": "assistant", "content": compact(plan_answer)}]})
    for call in run["calls"]:
        if call.get("skipped") or not call.get("response"):
            continue
        if call["kind"] == "ideas":
            ans = clean_ideas(call["response"], sc, target)
            if ans:
                out.append({"kind": "ideas", "messages": [
                    {"role": "system", "content": IDEAS_SYSTEM},
                    {"role": "user", "content": compact_prompt(call["prompt"])},
                    {"role": "assistant", "content": compact(ans)}]})
        elif call["kind"] == "explain":
            msgs = [{"role": "user", "content": call["prompt"]},
                    {"role": "assistant", "content": call["response"].strip()}]
            if call.get("system"):
                msgs.insert(0, {"role": "system", "content": call["system"]})
            out.append({"kind": "explain", "messages": msgs})
    return out, source


def build(tokenizer=None, max_tokens: int = 6144) -> dict:
    """Write sft/train.jsonl and sft/valid.jsonl. With a tokenizer, drop examples longer than max_tokens."""
    out_dir(SFT_DIR)
    splits = {"train": [], "valid": []}
    stats = Counter()
    for path in sorted(RUNS_DIR.glob("*.json")):
        run = json.loads(path.read_text())
        exs, source = examples_from_run(run)
        stats[f"runs:{source}"] += 1
        split = "valid" if run["split"] == "val" else "train"
        for ex in exs:
            if tokenizer is not None:
                n = len(tokenizer.apply_chat_template(ex["messages"], tokenize=True, return_dict=False))
                if n > max_tokens:
                    stats[f"too_long:{ex['kind']}"] += 1
                    continue
                stats["tokens"] += n
            ex["scenario"] = run["scenario"]["id"]
            splits[split].append(ex)
            stats[f"{split}:{ex['kind']}"] += 1
    for name, rows in splits.items():
        with open(SFT_DIR / f"{name}.jsonl", "w") as f:
            for ex in rows:
                f.write(json.dumps({"messages": ex["messages"]}) + "\n")     # mlx-lm reads "messages"
    (SFT_DIR / "stats.json").write_text(json.dumps(dict(stats), indent=1))
    return dict(stats)


if __name__ == "__main__":
    from transformers import AutoTokenizer
    from ml.local_planner.common import BASE_MODEL_DIR
    tok = AutoTokenizer.from_pretrained(str(BASE_MODEL_DIR))
    print(json.dumps(build(tok), indent=1))
