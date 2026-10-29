import io
import json
from unittest.mock import Mock

from dlubal_rfem_mcp.managed_bootstrap import read_connector_envelope
from dlubal_rfem_mcp.rfem.config import RFEMConfig
from dlubal_rfem_mcp.rfem.session import RFEMSession


def test_managed_configuration_ignores_credential_environment(envelope, monkeypatch):
    for name in ("DLUBAL_API_KEY", "RFEM_API_KEY", "RFEM_API_KEY_NAME"):
        monkeypatch.setenv(name, "must-not-import")
    parsed = read_connector_envelope(io.BytesIO(json.dumps(envelope).encode()))
    config = RFEMConfig.from_envelope(parsed)
    assert config.api_key.get_secret_value() == "fixture-manual"
    assert not hasattr(config, "api_key_name")


def test_session_calls_unmodified_application_with_value_only(monkeypatch):
    from dlubal.api.rfem import application

    app = Mock()
    factory = Mock(return_value=app)
    monkeypatch.setattr(application, "Application", factory)
    session = RFEMSession(api_key_value="fixture-manual", url="127.0.0.1", port=9000)
    with session.connection() as result:
        assert result is app
    assert factory.call_args.kwargs == {
        "api_key_value": "fixture-manual",
        "url": "127.0.0.1",
        "port": 9000,
    }
    app.close_connection.assert_called_once()


def test_missing_explicit_key_fails_before_sdk_lookup():
    import pytest

    with pytest.raises(ValueError, match="credentialMissing"):
        RFEMSession(api_key_value="", url="127.0.0.1", port=9000)
