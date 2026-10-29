from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dlubal_rfem_mcp.rfem.session import RFEMSession


@pytest.mark.parametrize(
    "actual",
    ["6.13.0013.249.hash", "6.14.0013.250.hash", "6.14.0013", "", None],
)
def test_managed_session_rejects_wrong_exact_rfem_build(monkeypatch, actual):
    from dlubal.api.rfem import application

    app = Mock()
    app.get_application_info.return_value = SimpleNamespace(
        version="6.14.0013", full_version=actual
    )
    monkeypatch.setattr(application, "Application", Mock(return_value=app))
    session = RFEMSession("fixture", "127.0.0.1", 9000, expected_build="6.14.0013.249")
    with (
        pytest.raises(ValueError, match="unsupportedSoftwareVersion"),
        session.connection(),
    ):
        pytest.fail("incompatible RFEM must not be yielded")
    app.close_connection.assert_called_once()


def test_managed_session_accepts_exact_rfem_build(monkeypatch):
    from dlubal.api.rfem import application

    app = Mock()
    app.get_application_info.return_value = SimpleNamespace(
        version="6.14.0013", full_version="6.14.0013.249.9fef45a93ad"
    )
    monkeypatch.setattr(application, "Application", Mock(return_value=app))
    session = RFEMSession("fixture", "127.0.0.1", 9000, expected_build="6.14.0013.249")
    with session.connection() as connected:
        assert connected is app
