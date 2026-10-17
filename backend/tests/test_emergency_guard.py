"""The 911 guard: emergency numbers and anything off the allowlist are refused."""

import pytest

from backend.emergency.guard import RefusedNumber, check_number, is_emergency_number, mask
from backend.emergency.phones import DryRunPhone, FakePhone
from backend.emergency.report import Report

ALLOW = {"+13525551234", "+13525559999"}
REPORT = Report("A", "S", "G", "W", "1:00 pm", "1 minute", "1:00 pm", "c", "h")


@pytest.mark.parametrize("number", [
    "911", "112", "999", "000", "988", "+1911", "1-911", "9911", " 9 1 1 ", "(911)", "+44 999", "+112",
])
def test_emergency_numbers_are_refused(number):
    assert is_emergency_number(number)
    with pytest.raises(RefusedNumber):
        check_number(number, ALLOW)


def test_911_is_refused_even_if_someone_puts_it_on_the_allowlist():
    with pytest.raises(RefusedNumber):
        check_number("911", {"911", "+1911"})


@pytest.mark.parametrize("number", ["+13525550000", "+18005551234", "", "abc", "12345"])
def test_numbers_off_the_allowlist_are_refused(number):
    with pytest.raises(RefusedNumber):
        check_number(number, ALLOW)


def test_allowlisted_number_passes_in_any_spelling():
    assert check_number("+1 (352) 555-1234", ALLOW) == "+13525551234"


def test_a_real_number_containing_911_is_fine_if_allowlisted():
    assert check_number("+1 352 911 1234", {"+13529111234"}) == "+13529111234"


def test_empty_allowlist_refuses_everything():
    with pytest.raises(RefusedNumber):
        check_number("+13525551234", set())


@pytest.mark.parametrize("phone_cls", [DryRunPhone, FakePhone])
def test_backends_never_dial_a_refused_number(phone_cls):
    phone = phone_cls(allowlist={"911", "+13525551234"})
    dialed = []
    phone._dial = lambda *a, **k: dialed.append(a)   # would record a real dial
    result = phone.call("911", "script", REPORT)
    assert result.status == "failed" and result.refused
    assert dialed == []


def test_mask_hides_most_digits():
    assert mask("+13525551234") == "+1 ••• ••• 1234"
