"""Shared test setup: no API key, no parallel processes, logs go to a temp folder."""

import pytest

from backend.learning import ace, parallel, settings


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv("PLANNER_LLM", "off")              # never call the real API from tests
    monkeypatch.setattr(parallel, "WORKERS", 1)           # run jobs in order (simpler, works with lambdas)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "COST_LOG", tmp_path / "cost_log.jsonl")
    monkeypatch.setattr(ace, "VERSIONS_DIR", tmp_path / "versions")
    yield
