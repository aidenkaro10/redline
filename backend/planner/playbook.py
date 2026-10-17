"""
The playbook: a list of short lessons the planner reads before planning (ACE-style).

A lesson has plain words for the LLM and, optionally, a structured "hint" the
optimizer can use as a SOFT preference. Hints can only nudge the schedule (penalties)
or add extra restrictions. They can never loosen a safety rule.

Safety filter (code, not LLM): a lesson is rejected if it mentions limits,
thresholds, temperatures, skipping breaks, or anyone "handling more". Ignored alerts
never teach tolerance. Only whitelisted hint types are allowed.
"""

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).parent
# Where the learned playbook lives. Session F (backend/learning/) writes it; the planner only reads it.
# Override with PLANNER_PLAYBOOK=path/to/playbook.json.
LEARNED_PATH = Path(os.getenv("PLANNER_PLAYBOOK", HERE.parent / "learning" / "playbook.json"))
MAX_LESSONS = 40

# Words that would let a lesson touch safety. Any match = rejected, no exceptions.
FORBIDDEN = [
    r"\blimit", r"threshold", r"\b3[6-9](\.\d+)?\s*°?\s*c\b", r"°", r"core temp", r"\bp90\b", r"percentile",
    r"handle more", r"can handle", r"tolerat", r"skip(ping)? (the |a )?break", r"fewer breaks", r"no breaks?",
    r"shorter breaks?", r"ignore", r"override", r"lightning", r"all[- ]clear", r"stop[- ]work", r"wbgt\s*[<>=]",
    r"acclimati[sz]ed? (enough|now)", r"rule checker", r"budget",
]
HINT_TYPES = {"avoid_intensity"}          # the only structured hint the optimizer accepts from lessons
WHO = {"all", "unacclimatized"}           # or a worker id / name


@dataclass
class Lesson:
    id: str
    text: str                              # plain words, e.g. "Schedule new hires' heavy work before 11:00."
    hint: dict | None = None               # {"type": "avoid_intensity", "who": "unacclimatized",
                                           #  "intensities": ["heavy", "very_heavy"], "from_hour": 11, "penalty": 20}
    source: str = ""                       # which day / reflection it came from
    uses: int = 0
    helped: int = 0


def is_safe_lesson(lesson: Lesson) -> tuple[bool, str]:
    """Code filter. Returns (ok, reason)."""
    text = lesson.text.lower()
    for pat in FORBIDDEN:
        if re.search(pat, text):
            return False, f"mentions a safety topic ({pat})"
    if lesson.hint is not None:
        h = lesson.hint
        if h.get("type") not in HINT_TYPES:
            return False, f"hint type {h.get('type')!r} not allowed"
        if not set(h.get("intensities", [])) <= {"light", "moderate", "heavy", "very_heavy"}:
            return False, "unknown intensity"
        if not (0 < int(h.get("penalty", 0)) <= 50):
            return False, "penalty must be 1..50"
        if not (0 <= float(h.get("from_hour", 0)) <= 24 and 0 <= float(h.get("to_hour", 24)) <= 24):
            return False, "bad hours"
    if len(lesson.text) > 300:
        return False, "too long (small, specific lessons only)"
    return True, "ok"


@dataclass
class Playbook:
    lessons: list[Lesson] = field(default_factory=list)
    version: int = 0
    history: list[dict] = field(default_factory=list)   # every change: what, why, gate result

    def add(self, lesson: Lesson) -> tuple[bool, str]:
        ok, why = is_safe_lesson(lesson)
        if not ok:
            return False, why
        if any(_similar(lesson.text, l.text) for l in self.lessons):
            return False, "near-duplicate of an existing lesson"
        if len(self.lessons) >= MAX_LESSONS:
            return False, "playbook full"
        self.lessons.append(lesson)
        return True, "added"

    def text(self) -> str:
        if not self.lessons:
            return "(no lessons yet)"
        return "\n".join(f"[{l.id}] {l.text}" for l in self.lessons)

    def soft_preferences(self, scenario) -> tuple[list[dict], list[str]]:
        """Turn lesson hints into optimizer soft preferences for this scenario."""
        from backend import config
        prefs, used = [], []
        h0, m0 = map(int, scenario.shift_start.split(":"))
        for l in self.lessons:
            h = l.hint
            if not h or not is_safe_lesson(l)[0]:
                continue
            who = h.get("who", "all")
            if who == "all":
                workers = scenario.crew
            elif who == "unacclimatized":
                workers = [w for w in scenario.crew if w.acclim < config.PLAN_UNACCLIM_LEVEL]
            else:
                workers = [w for w in scenario.crew if w.id == who or w.name.split(" ")[0] == who]
            to_slot = lambda hour: max(0, int((hour * 60 - (h0 * 60 + m0)) // scenario.slot_min))
            for w in workers:
                prefs.append({"worker": w.id, "intensities": h["intensities"],
                              "from_slot": to_slot(h.get("from_hour", 0)), "to_slot": to_slot(h.get("to_hour", 24)),
                              "penalty": int(h["penalty"]), "lesson": l.id})
            if workers:
                used.append(l.id)
        return prefs, used

    def save(self, path: Path = LEARNED_PATH) -> None:
        path.write_text(json.dumps({"version": self.version, "lessons": [asdict(l) for l in self.lessons],
                                    "history": self.history}, indent=1))

    @classmethod
    def load(cls, path: Path) -> "Playbook":
        d = json.loads(Path(path).read_text())
        return cls([Lesson(**l) for l in d["lessons"]], d.get("version", 0), d.get("history", []))

    @classmethod
    def load_learned(cls) -> "Playbook | None":
        return cls.load(LEARNED_PATH) if LEARNED_PATH.exists() else None


def _similar(a: str, b: str) -> bool:
    """Near-duplicate check: share most of their words."""
    wa, wb = set(re.findall(r"\w+", a.lower())), set(re.findall(r"\w+", b.lower()))
    return len(wa & wb) / max(1, min(len(wa), len(wb))) > 0.8
