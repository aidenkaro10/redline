"""pytest picks up the e2e fixtures from here. The tools themselves live in e2e_support.py."""

from e2e_support import fresh_hazard_state, hazard_files_in_tmp, live_app, no_network  # noqa: F401  (fixtures)

import pytest

from backend import config


@pytest.fixture(autouse=True)
def no_local_worker_name(monkeypatch):
    """Tests must not depend on the developer's .env: WORKER_NAME there names the live band's wearer."""
    monkeypatch.setattr(config, "WORKER_NAME", "")
