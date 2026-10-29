import pytest

from dlubal_rfem_mcp.core.identity import connector_build_id


@pytest.fixture
def envelope(tmp_path):

    return {
        "schemaVersion": 3,
        "binding": {
            "packageId": "dlubal-rfem",
            "releaseDigest": "sha256:" + "a" * 64,
            "connectorInstanceId": "ci_" + "1" * 32,
            "generation": 1,
            "configurationRevision": 1,
            "secretRevision": 1,
        },
        "mode": "runtime",
        "bind": {"host": "127.0.0.1", "port": 8765, "path": "/mcp"},
        "bearerToken": "b" * 40,
        "configuration": {},
        "secrets": [{"fieldId": "dlubal-api-key", "value": "fixture-manual"}],
        "dataDirectory": str(tmp_path),
        "connectorBuildId": connector_build_id(),
        "nativePeer": None,
    }