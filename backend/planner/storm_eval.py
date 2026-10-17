"""
Does the storm-chance soft cost move risky outdoor work out of likely-storm hours, and what does it cost?

On every held-out test scenario with a storm window (the frozen files predate storm chances, so
the evening-before forecast is filled in the same way the generator does it), plan twice with the
deterministic optimizer: storm cost off vs on. Count outdoor work units planned in slots with a
>= 40% storm chance (all outdoor, and elevated / hard-to-stop / heavy ones), and work done.

Run:  python -m backend.planner.storm_eval
"""

from dataclasses import replace

from backend.planner.generate import add_storm_forecast, load_test_set
from backend.planner.optimizer import OBJECTIVES, optimize
from backend.planner.proactive import weighted_done
from backend.planner.scenario import REST
from backend.planner.storm_risk import storm_reason

RISKY_P = 0.4


def exposure(sc, plan) -> tuple[int, int]:
    """(outdoor units in risky slots, of those: elevated, hard-to-stop or heavy)"""
    allu = sens = 0
    for row in plan.assignments.values():
        for k, a in enumerate(row):
            if a == REST or sc.weather[k].storm_prob < RISKY_P:
                continue
            t = sc.task(a)
            if t.outdoor:
                allu += 1
                sens += int(t.elevated or t.hard_to_stop or t.intensity in ("heavy", "very_heavy"))
    return allu, sens


def main():
    rows = []
    for sc in load_test_set():
        add_storm_forecast(sc.weather, sc.hazards)
        if not any(w.storm_prob >= RISKY_P for w in sc.weather):
            continue
        out = {"scenario": sc.id}
        for name, weight in (("off", 0), ("on", OBJECTIVES["throughput"].storm)):
            r = optimize(sc, replace(OBJECTIVES["throughput"], storm=weight), deterministic=True)
            a, s = exposure(sc, r.plan)
            out[name] = {"outdoor_units_in_storm": a, "sensitive_units_in_storm": s,
                         "work_done": round(weighted_done(sc, r.plan), 4), "verified": r.verified}
            if name == "on":
                out["reason"] = storm_reason(sc, r.plan)
        rows.append(out)
        print(sc.id, out["off"], out["on"], "\n   ", out["reason"])
    tot = lambda arm, k: sum(r[arm][k] for r in rows)
    print(f"\n{len(rows)} scenarios with a >= {RISKY_P:.0%} storm chance")
    for arm in ("off", "on"):
        print(f"storm cost {arm:3s}: outdoor units in storm slots {tot(arm, 'outdoor_units_in_storm')}, "
              f"sensitive {tot(arm, 'sensitive_units_in_storm')}, "
              f"work done {sum(r[arm]['work_done'] for r in rows) / len(rows):.1%}, "
              f"verified {sum(r[arm]['verified'] for r in rows)}/{len(rows)}")
    return rows


if __name__ == "__main__":
    main()
