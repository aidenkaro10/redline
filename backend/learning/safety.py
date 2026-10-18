"""
Code filters that keep every learned thing away from safety. No LLM in here.

Two layers for playbook lessons:
  1. The planner's own filter (backend/planner/playbook.py is_safe_lesson): rejects text about
     limits, thresholds, temperatures, skipping breaks, "can handle more", lightning, and any
     hint type except avoid_intensity (which can only ADD penalties, never loosen a rule).
  2. Ours, stricter (below): also rejects anything that pushes people harder or longer,
     treats someone as "used to it", or talks about alerts. Ignored alerts never teach tolerance.

The same filter checks GEPA's evolved strategy notes, so an evolved prompt can't argue
for less caution either. (It couldn't change a rule anyway: the rule checker sits
outside everything that evolves. This is belt and braces.)
"""

import re

from backend.planner.playbook import Lesson, is_safe_lesson

EXTRA_FORBIDDEN = [
    r"\bmore (heat|work|load|hours|exposure)\b", r"\bpush(ed|ing)?\b", r"\b(work|push|go|try) harder\b", r"\blonger (shift|stretch|run|block)",
    r"without (a |the )?(break|rest|shade)", r"\bused to (it|the heat)\b", r"\btough\b", r"\bstrong enough\b",
    r"\balert", r"\balarm", r"\bsafety\b", r"\bsafe to\b", r"\brelax", r"\bloosen", r"\braise\b",
    r"\bincrease (the )?(cap|max|allow)", r"\bextend", r"\bdisable", r"\bbypass", r"\bheart rate (is )?fine\b",
    r"\bfine in the heat\b", r"\bno need\b", r"\bdon'?t need\b", r"\bkeep working\b", r"\bwork through\b",
]


def lesson_ok(lesson: Lesson) -> tuple[bool, str]:
    ok, why = is_safe_lesson(lesson)
    if not ok:
        return False, f"planner filter: {why}"
    text = lesson.text.lower()
    for pat in EXTRA_FORBIDDEN:
        if re.search(pat, text):
            return False, f"learning filter: touches safety or 'can handle more' ({pat})"
    h = lesson.hint
    if h is not None:
        if set(h) - {"type", "who", "intensities", "from_hour", "to_hour", "penalty"}:
            return False, "learning filter: unknown hint fields"
        if not h.get("intensities"):
            return False, "learning filter: hint names no intensities"
    return True, "ok"


def note_ok(note: str) -> tuple[bool, str]:
    """Filter for GEPA's evolved strategy note (free text added to the planner's prompt)."""
    if len(note) > 400:
        return False, "too long"
    return lesson_ok(Lesson(id="note", text=note[:300]))
