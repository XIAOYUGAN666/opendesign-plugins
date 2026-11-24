import pytest

from dlubal_mcp.rfem import stale_release


@pytest.fixture(autouse=True)
def application_session_still_open(monkeypatch):
    """Release depends on real processes; tests opt in to an ended session.

    Without this, a test would pass or fail depending on whether RFEM happens
    to be running on the machine that runs it.
    """
    monkeypatch.setattr(stale_release, "start_times", lambda prefix: None)
