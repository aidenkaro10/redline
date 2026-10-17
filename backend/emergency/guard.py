"""
The 911 guard. Every phone call goes through check_number() first.

Rules (all must pass, or the call is refused):
1. The number is not an emergency short code (911, 112, 999, ...).
2. It has at least EMERGENCY_MIN_DIGITS digits (so no short codes sneak by,
   like "1-911" or "9-911").
3. It is on the allowlist built from .env (crew lead, safety officer, test phone).
"""

import re

from backend import config


class RefusedNumber(Exception):
    """Raised when code tries to call a number it must not call."""


def digits_only(number: str) -> str:
    """'+1 (352) 555-1234' -> '13525551234'."""
    return re.sub(r"\D", "", number or "")


def normalize(number: str) -> str:
    """Turn a number into '+<digits>' so two spellings of one number match."""
    return "+" + digits_only(number)


def is_emergency_number(number: str) -> bool:
    """True for 911, 112, 999 and friends, however they're written."""
    d = digits_only(number)
    if d in config.EMERGENCY_BLOCKED_NUMBERS:
        return True
    # Short codes with a prefix in front, e.g. "1911" or "9911" (dial 9 for a line).
    return len(d) < config.EMERGENCY_MIN_DIGITS and any(d.endswith(b) for b in config.EMERGENCY_BLOCKED_NUMBERS)


def default_allowlist() -> set[str]:
    """The numbers from .env that we're allowed to call. Empty ones are skipped."""
    numbers = [config.CREW_LEAD_NUMBER, config.SAFETY_OFFICER_NUMBER, config.EMERGENCY_TEST_NUMBER]
    return {normalize(n) for n in numbers if n and not is_emergency_number(n)}


def check_number(number: str, allowlist: set[str] | None = None) -> str:
    """
    Return the cleaned-up number if it's safe to call. Otherwise raise RefusedNumber.
    """
    if allowlist is None:
        allowlist = default_allowlist()
    if not number or not digits_only(number):
        raise RefusedNumber("No number given.")
    if is_emergency_number(number):
        raise RefusedNumber(f"Refused: {number} is an emergency number. The prototype never calls 911.")
    if len(digits_only(number)) < config.EMERGENCY_MIN_DIGITS:
        raise RefusedNumber(f"Refused: {number} is too short to be a real phone number.")
    clean = normalize(number)
    # The allowlist itself is cleaned too, in case 911 was typed into .env.
    safe_list = {normalize(n) for n in allowlist if not is_emergency_number(n)}
    if clean not in safe_list:
        raise RefusedNumber(f"Refused: {number} is not on the allowlist in .env.")
    return clean


def mask(number: str) -> str:
    """Hide most digits for the dashboard: '+13525551234' -> '+1 ••• ••• 1234'."""
    d = digits_only(number)
    if len(d) < 4:
        return "•••"
    country = d[:-10] if len(d) > 10 else ""
    return f"+{country} ••• ••• {d[-4:]}".replace("+ ", "+") if country else f"••• ••• {d[-4:]}"
