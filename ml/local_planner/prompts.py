"""
Make the planner's prompts shorter for the small local model, without changing what they say.

The agent's "ideas" prompt includes the best optimizer plan as a "plan view": one dict per worker
per slot with the clock time and task name repeated every time. That's ~2,500 tokens of a ~4,000
token prompt. Here we swap it for the same facts in compact form:
    {"source", "verified", "peak_p90_c": {worker: °C}, "assignments": {worker: [task ids]},
     "tasks": [{"id", "done", "needed"}]}
Slot times and task names are already in the scenario part of the prompt.

Used in BOTH places, so training and running see the same text:
  - dataset.py, when it builds the "ideas" training examples
  - client.LocalLLM.text, right before the model reads a prompt
Anything we can't parse is left exactly as it was.
"""

import json

PLAN_START = "Best optimizer plan so far:\n"
PLAN_END = "\n\nSuggest up to"


def compact_plan_view(view: dict) -> dict:
    return {
        "source": view.get("source"),
        "verified": view.get("verified"),
        "peak_p90_c": {w["id"]: w.get("peak_p90_c") for w in view.get("workers", [])},
        "assignments": {w["id"]: [s["task"] for s in w.get("slots", [])] for w in view.get("workers", [])},
        "tasks": [{"id": t["id"], "done": t["done"], "needed": t["needed"]} for t in view.get("tasks", [])],
    }


def compact_prompt(prompt: str) -> str:
    """Shrink the plan view inside an "ideas" prompt. Other prompts come back unchanged."""
    i = prompt.find(PLAN_START)
    j = prompt.find(PLAN_END, i + 1) if i >= 0 else -1
    if i < 0 or j < 0:
        return prompt
    try:
        view = json.loads(prompt[i + len(PLAN_START):j])
        small = json.dumps(compact_plan_view(view), separators=(",", ":"))
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return prompt
    return prompt[:i + len(PLAN_START)] + small + prompt[j:]
