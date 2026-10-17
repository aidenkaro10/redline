"""
A fake Claude for tests and offline dry runs. Never touches the network, costs $0.

It answers each kind of prompt the learning loops (and the planner agent) send with a
fixed, plausible JSON reply. Some replies are deliberately BAD (an unsafe lesson, an
out-of-range setting, an impossible scenario) so tests can prove the code filters
catch them.

Each prompt the loops write starts with a "TASK: <NAME>" line, which is how the fake
knows what to answer.
"""

import json


class FakeLLM:
    model = "fake"

    def __init__(self):
        self.cost_usd = 0.0
        self.calls = 0
        self.prompts = []           # every prompt it saw (tests read these)

    def text(self, prompt, system=None, cache_key=None, max_tokens=None, effort=None):
        self.calls += 1
        self.cost_usd += 0.001      # pretend each call costs a tenth of a cent (tests check the budget math)
        self.prompts.append(prompt)
        if prompt.startswith("TASK: PROPOSE"):
            return json.dumps({"specs": [
                {"seed": 501, "crew": "home", "heat_c": 4.0, "heavy_bias": 0.6, "family": "heat_dome"},
                {"seed": 502, "crew": "home", "storm_start": 13, "storm_len": 2, "family": "storm_2pm"},
                {"seed": 503, "crew": "random", "new_hires": 1, "heat_c": 2.0, "family": "new_hire"},
                {"seed": 504, "crew": "home", "dead_watch": True, "heat_c": 1.0, "family": "dead_watch"},
                {"seed": 505, "crew": "home", "heat_c": 99, "family": "heat_dome"},          # out of range: rejected
                {"seed": 506, "crew": "home", "callouts": 1, "demand": 0.9, "family": "callout"},
            ]})
        if prompt.startswith("TASK: REFLECT"):
            return json.dumps({"root_cause": "The new hire ran hotter than forecast on heavy work in the "
                                             "afternoon. Heavy work late in the day is the problem.",
                               "insights": ["Move new hires' heavy work to the morning."]})
        if prompt.startswith("TASK: CURATE"):
            return json.dumps({"operations": [
                {"op": "add", "text": "Schedule new hires' heavy work before 11:00.",
                 "hint": {"type": "avoid_intensity", "who": "unacclimatized", "intensities": ["heavy", "very_heavy"],
                          "from_hour": 11, "penalty": 20}},
                # unsafe on purpose: the code filter must reject it
                {"op": "add", "text": "Kofi can handle more heat now, skip the extra break.", "hint": None},
            ]})
        if prompt.startswith("TASK: MUTATE"):
            return json.dumps({"why": "Afternoon alerts: rank plans with more weight on peak heat.",
                               "changes": {"w_peak": 2.0, "objectives": ["balanced", "margin", "throughput"],
                                           "n_llm_ideas": 99},                      # 99 is clamped by code
                               "strategy_note": "Prefer plans that finish heavy outdoor work early."})
        # The planner agent's own prompts (backend/planner/archon.py)
        if "strategy ideas" in prompt:
            return json.dumps({"ideas": []})
        if "complaints" in prompt:
            return json.dumps({"complaints": []})
        return "- Safe plan.\n- Heavy work early.\n- Breaks in the shade."
