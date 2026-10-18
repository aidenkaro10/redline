"""
Are the crew-lead explanations factually right? A plain-code fact check, no LLM judge.

The agent asks the LLM to explain a plan from a list of facts (peak temperature, margin, work done,
stop-work times, new hires, alternatives). Every concrete claim in the answer must be backed by those
facts or by the day itself:
  - percentages        must match a percentage in the facts (within 2.5 points: "about 60%" is fine)
  - temperatures (°C)  must match a temperature in the facts or the day's WBGT/limit (within 0.05)
  - clock times        must be a time in the facts (13:00 = 1 PM = 1 p.m.) or a stop-work window edge
  - worker names       must be on the crew
Anything else counts as UNSUPPORTED (invented or wrong). Words like "about two-thirds" aren't checked.

Fair comparison with Claude at $0: on the 28 VALIDATION days (never trained on) the teacher saved
Claude's explanation AND the exact facts it was given. The local model gets the same prompt.

Run:  python -m ml.local_planner.explain_check --adapter adapter
"""

import argparse
import json
import re
from pathlib import Path

from ml.local_planner.common import BASE_MODEL_DIR, CKPT_DIR, DATA_DIR, out_dir

from backend.planner.generate import NAMES
from backend.planner.scenario import Scenario
from ml.local_planner.teacher import RUNS_DIR

PCT_TOL = 2.5          # "about 60%" for 62% is fine; 93% for 71% is not
OTHER_NAMES = ["Rosa", "Dev", "Priya", "Emma", "Sam", "Wei", "Jo", "Ike", "Lena", "Omar", "Marcus", "Ana", "Luis",
               "Jada", "Tom", "Kofi"]
PCT = re.compile(r"(\d{1,3}(?:\.\d)?)\s?(?:%|percent)")
TEMP = re.compile(r"(\d{2}\.\d{1,2})\s?°?\s?C\b|(\d{2}\.\d{1,2})\s?degrees")
CLOCK24 = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
CLOCK12 = re.compile(r"\b(1[0-2]|[1-9])(?::([0-5]\d))?\s?(a\.?m\.?|p\.?m\.?|AM|PM)", re.I)
RANGE12 = re.compile(r"\b(1[0-2]|[1-9])(?::([0-5]\d))?\s?(?:to|-|–|until)\s?(1[0-2]|[1-9])(?::([0-5]\d))?\s?(a\.?m\.?|p\.?m\.?|AM|PM)", re.I)


def _minutes_12(h: str, m: str | None, ampm: str) -> int:
    h, m = int(h), int(m or 0)
    pm = ampm.lower().startswith("p")
    if pm and h != 12:
        h += 12
    if not pm and h == 12:
        h = 0
    return h * 60 + m


def times_in(text: str) -> list[int]:
    """Clock times in minutes after midnight: "1 to 2 PM", "2:30 p.m.", "13:00"."""
    out = []
    for h1, m1, h2, m2, ampm in RANGE12.findall(text):        # "1 to 2 PM": both ends share the PM
        out += [_minutes_12(h1, m1, ampm), _minutes_12(h2, m2, ampm)]
    text = RANGE12.sub(" ", text)
    out += [_minutes_12(h, m, ap) for h, m, ap in CLOCK12.findall(text)]
    text = CLOCK12.sub(" ", text)                             # so "2:30 PM" isn't also read as 02:30
    # bare "3:00": shifts run in daytime, so hours 1-6 mean the afternoon (3:00 = 15:00)
    out += [(int(h) + (12 if 1 <= int(h) <= 6 else 0)) * 60 + int(m) for h, m in CLOCK24.findall(text)]
    return out


def check(answer: str, facts: str, sc: Scenario) -> dict:
    """Count claims in `answer` and how many aren't backed by `facts` or the day."""
    fact_pcts = [float(x) for x in PCT.findall(facts)]
    fact_temps = [float(a or b) for a, b in TEMP.findall(facts)] + [38.0] + [w.wbgt for w in sc.weather]
    fact_nums = [float(x) for x in re.findall(r"\d{2}\.\d{1,3}", facts)]          # e.g. "37.93" without °C
    fact_times = set(times_in(facts))
    h0, m0 = map(int, sc.shift_start.split(":"))
    for hz in sc.hazards:                                                          # stop-work window edges
        for slot in (hz.start_slot, hz.end_slot):
            fact_times.add(h0 * 60 + m0 + slot * sc.slot_min)
    for slot in sc.stop_work_slots() | {s + 1 for s in sc.stop_work_slots()}:
        fact_times.add(h0 * 60 + m0 + slot * sc.slot_min)
    crew = {w.name.split(" ")[0] for w in sc.crew}

    claims, bad = [], []
    for x in PCT.findall(answer):
        v = float(x)
        claims.append(("pct", v))
        if not any(abs(v - f) <= PCT_TOL for f in fact_pcts):
            bad.append(("pct", v))
    for a, b in TEMP.findall(answer):
        v = float(a or b)
        ok = any(abs(v - f) <= 0.05 for f in fact_temps + fact_nums)
        claims.append(("temp", v))
        if not ok:
            bad.append(("temp", v))
    for t in times_in(answer):
        claims.append(("time", t))
        if t not in fact_times:
            bad.append(("time", f"{t // 60:02d}:{t % 60:02d}"))
    for name in sorted(set(NAMES) | set(OTHER_NAMES)):
        if re.search(rf"\b{name}\b", answer):
            claims.append(("name", name))
            if name not in crew:
                bad.append(("name", name))
    return {"claims": len(claims), "unsupported": len(bad), "bad": bad}


def val_items() -> list[dict]:
    """Validation days: Claude's explain prompt (the facts) and Claude's answer."""
    items = []
    for path in sorted(RUNS_DIR.glob("*.json")):
        run = json.loads(path.read_text())
        if run["split"] != "val":
            continue
        call = next((c for c in run["calls"] if c.get("kind") == "explain" and c.get("response")), None)
        if call:
            items.append({"scenario": run["scenario"], "prompt": call["prompt"], "claude": call["response"]})
    return items


def facts_of(prompt: str) -> str:
    return prompt.split("Facts:", 1)[-1]


def summarize(rows: list[dict], key: str) -> dict:
    claims = sum(r[key]["claims"] for r in rows)
    bad = sum(r[key]["unsupported"] for r in rows)
    return {"answers": len(rows), "claims": claims, "unsupported": bad,
            "unsupported_share": bad / claims if claims else 0.0,
            "answers_with_any_unsupported": sum(r[key]["unsupported"] > 0 for r in rows) / len(rows)}


def heldout(results_path: Path, keys: dict) -> dict:
    """
    Held-out days (B's 12 test days), from the pipeline eval results. Claude's exact facts weren't
    saved there, so BOTH models are checked against the same thing: the final plan's facts (the
    summary + the code-written facts line). Claims about the alternatives can't be confirmed that
    way, so they count as unsupported for both. For local models the full facts were saved too.
    """
    from backend.planner.generate import load_test_set
    raw = json.loads(Path(results_path).read_text())
    scs = {sc.id: sc for sc in load_test_set()}
    out = {}
    for label, key in keys.items():
        rows, full = [], []
        for r in raw.get(key, []):
            sc = scs[r["scenario"]]
            if "ai_notes_raw" in r:                         # newer runs: LLM bullets kept apart, reasons = code facts
                bullets = "\n".join(r["ai_notes_raw"])
                plan_facts = r["summary"] + "\n" + "\n".join(r["reasons"])
            else:                                           # older runs: LLM bullets + the code facts line last
                bullets = "\n".join(r["reasons"][:-1])
                plan_facts = r["summary"] + "\n" + r["reasons"][-1]
            rows.append({key: check(bullets, plan_facts, sc)})
            if r.get("explain_facts"):
                full.append({key: check(bullets, r["explain_facts"], sc)})
        out[label] = {"vs_final_plan_facts": summarize(rows, key) if rows else None,
                      "vs_full_facts": summarize(full, key) if full else None}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default="adapter")
    ap.add_argument("--recheck", action="store_true", help="re-score saved answers, no generation")
    ap.add_argument("--heldout", default="", help="pipeline eval results.json to check (held-out days)")
    args = ap.parse_args()
    out = out_dir(DATA_DIR / "explain_check")
    if args.heldout:
        res = heldout(Path(args.heldout), {"Claude agent": "pipeline:claude", "local base": "pipeline:base",
                                           "local adapter": "pipeline:finetuned"})
        (out / "heldout.json").write_text(json.dumps(res, indent=1))
        print(json.dumps(res, indent=1))
        return
    if args.recheck:
        res = json.loads((out / f"val_{args.adapter}.json").read_text())
        scs = {it["scenario"]["id"]: Scenario.from_dict(it["scenario"]) for it in val_items()}
        for r in res["rows"]:
            for who in ("claude", "local"):
                r[who] = check(r[f"{who}_text"], r["facts"], scs[r["scenario"]])
        res["claude"], res["local"] = summarize(res["rows"], "claude"), summarize(res["rows"], "local")
        (out / f"val_{args.adapter}.json").write_text(json.dumps(res, indent=1))
        print(json.dumps({"claude": res["claude"], f"local ({args.adapter})": res["local"]}, indent=1))
        return
    from ml.local_planner.client import LocalLLM
    llm = LocalLLM(adapter_dir=CKPT_DIR / args.adapter if args.adapter != "none" else None)
    rows = []
    for it in val_items():
        sc = Scenario.from_dict(it["scenario"])
        mine = llm.text(it["prompt"], effort="low")
        f = facts_of(it["prompt"])
        rows.append({"scenario": sc.id, "facts": f, "claude_text": it["claude"], "local_text": mine,
                     "claude": check(it["claude"], f, sc), "local": check(mine, f, sc)})
    out = out_dir(DATA_DIR / "explain_check")
    res = {"claude": summarize(rows, "claude"), "local": summarize(rows, "local"), "rows": rows}
    (out / f"val_{args.adapter}.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({"claude": res["claude"], f"local ({args.adapter})": res["local"]}, indent=1))


if __name__ == "__main__":
    main()
