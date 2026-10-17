"""
PlannerWeights: every SOFT knob the planner has, in one place, so session F can search them.

What's in here (all trade-offs, none of them safety):
  optimizer    how much work, heat margin and fairness count; how far under the limit to plan;
               task priority values; storm/rain chance costs; preference for extra breaks in heat
  forecasts    the early line and horizon the act-on-forecast loop watches

What's NOT in here, ever: the heat limit, break rules, lightning / NWS stop-work, new-hire rule,
personal-offset caps, rate limits. Those live in backend/config.py and the rule checker, and
every plan still has to pass the rule checker whatever the weights say.

Two guards, enforced by validate():
  - early_line_c can't be above the yellow line (config.PLAN_MAX_P90_C): acting early can be tuned,
    acting late can't.
  - budget_margin_c can't be negative: the optimizer may plan closer to the limit, never past it.

Defaults reproduce the planner's behavior before this file existed.
"""

from dataclasses import asdict, dataclass, field, fields

from backend import config


@dataclass
class PlannerWeights:
    # --- optimizer objective (the "throughput" plan; the agent's other variants add on top) ---
    throughput: int = 10                # weight on priority-weighted work done
    margin: int = 0                     # weight on keeping everyone's peak heat low
    fairness: int = 0                   # weight on keeping peaks similar across the crew
    budget_margin_c: float = 0.05       # plan this far under the heat limit (°C, >= 0)
    priority_values: dict = field(default_factory=lambda: {1: 10, 2: 5, 3: 2})   # value of one work unit
    storm: int = 15                     # cost of outdoor work per unit of storm chance (x2 elevated / hard-to-stop)
    rain: int = 0                       # cost of outdoor work per unit of rain chance
    hot_break_bonus: int = 0            # reward per rest slot when WBGT >= PLAN_HOT_WBGT_C (more breaks in heat)
    # --- acting on forecasts (proactive.py) ---
    early_line_c: float = config.PLAN_EARLY_LINE_C       # act when the forecast 90th pct reaches this (<= yellow line)
    early_horizon_min: int = config.PLAN_EARLY_HORIZON_MIN   # which forecast minute to look at (1-30)

    def validate(self) -> "PlannerWeights":
        """Raise ValueError if a weight would cross into safety territory or make no sense."""
        if self.early_line_c > config.PLAN_MAX_P90_C:
            raise ValueError(f"early_line_c {self.early_line_c} is above the yellow line {config.PLAN_MAX_P90_C}")
        if self.budget_margin_c < 0:
            raise ValueError("budget_margin_c can't be negative (that would plan past the heat limit)")
        if not 1 <= self.early_horizon_min <= 30:
            raise ValueError("early_horizon_min must be 1-30 (the forecaster's horizon)")
        for name in ("throughput", "margin", "fairness", "storm", "rain", "hot_break_bonus"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} can't be negative")
        if self.throughput <= 0:
            raise ValueError("throughput must be positive, or the plan does no work")
        if set(self.priority_values) != {1, 2, 3} or min(self.priority_values.values()) <= 0:
            raise ValueError("priority_values needs positive values for priorities 1, 2 and 3")
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PlannerWeights":
        known = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in known}
        if "priority_values" in d:
            d["priority_values"] = {int(k): v for k, v in d["priority_values"].items()}
        return cls(**d).validate()


DEFAULT = PlannerWeights()
