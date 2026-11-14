from contextlib import contextmanager
from types import SimpleNamespace

from dlubal_rfem_mcp import setup
from dlubal_rfem_mcp.rfem import session


def test_setup_matches_complete_build_without_reading_key():
    assert setup.select_sdk("6.14.0013.249") == "2.14.13"
    assert setup.select_sdk("6.14.0013") is None
    assert setup.select_sdk("6.14.0013.250") is None


def test_diagnosis_uses_one_service_query_and_does_not_return_credentials(tmp_path, monkeypatch):
    calls = []

    @contextmanager
    def connect(*, service_query):
        assert service_query
        yield SimpleNamespace(get_application_info=lambda: calls.append("Service"))

    monkeypatch.setattr(setup, "prepared_build", lambda _: "6.14.0013.249")
    monkeypatch.setattr(session, "RFEMSession", lambda *_args, **_kwargs: SimpleNamespace(
        connection=connect, validate_application_info=lambda _info: None,
    ))
    result = setup.diagnose(tmp_path, "test-key")
    assert result["type"] == "completed"
    assert "6.14.0013.249" in result["message"]
    assert "test-key" not in result["message"]
    assert calls == ["Service"]
