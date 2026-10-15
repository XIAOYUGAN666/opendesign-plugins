import pytest

from dlubal_rfem_mcp.core.identity import provider_build_id


@pytest.fixture
def envelope(tmp_path):
    return {
        "schemaVersion": 2,
        "mode": "execution",
        "connectorInstanceId": "ci_" + "1" * 32,
        "generation": 1,
        "bind": {"host": "127.0.0.1", "port": 8765, "path": "/mcp"},
        "bearerToken": "test_bearer_" + "b" * 40,
        "configuration": {"dlubal-api-key": "fixture-manual"},
        "data": {"stateDirectory": str(tmp_path)},
        "software": {"safeIdentity": "si_" + "2" * 32, "build": "6.14.0013.249"},
        "providerBuildId": provider_build_id(),
    }
