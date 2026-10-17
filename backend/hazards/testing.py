"""Shared pytest fixture: point hazard caches and logs at a temp folder."""

import pytest

from backend.hazards import report_log, store
from backend.hazards.cache import clear_all_caches


@pytest.fixture(autouse=True)
def hazard_tmp_store(tmp_path):
    old_mode = store.get_mode()
    store.set_data_dir(tmp_path)
    report_log.reset_log(tmp_path / "hazard_log.jsonl")
    clear_all_caches()
    yield tmp_path
    clear_all_caches()
    store.set_mode(old_mode)
    report_log.reset_log()
