import copy
import io
import json

import pytest

from dlubal_rfem_mcp.managed_bootstrap import BootstrapError, read_envelope


def test_exact_envelope_reads_manual_secret_and_redacts_representation(envelope):
    parsed = read_envelope(io.BytesIO(json.dumps(envelope).encode()))
    assert parsed.configuration.api_key.get_secret_value() == "fixture-manual"
    assert parsed.bind.port == 8765
    for secret in (
        envelope["configuration"]["dlubal-api-key"],
        envelope["bearerToken"],
    ):
        assert secret not in repr(parsed)
        assert secret not in parsed.model_dump_json()


@pytest.mark.parametrize(
    "key,value",
    [
        ("schemaVersion", 1),
        ("generation", 0),
        ("generation", True),
        ("connectorInstanceId", "../x"),
        ("providerBuildId", "unknown"),
        ("unexpected", "secret"),
    ],
)
def test_rejects_invalid_top_level(envelope, key, value):
    envelope[key] = value
    with pytest.raises(BootstrapError, match="invalidStartupEnvelope"):
        read_envelope(io.BytesIO(json.dumps(envelope).encode()))


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("bind", "host", "localhost"),
        ("bind", "host", "0.0.0.0"),
        ("bind", "port", 80),
        ("bind", "port", 65536),
        ("bind", "path", "/mcp/sse"),
        ("configuration", "dlubal-api-key", ""),
        ("configuration", "fieldId", "other"),
        ("configuration", "revision", 0),
        ("configuration", "apiKeyName", "default"),
        ("software", "build", "6.13.0000.0"),
        ("data", "stateDirectory", "../secret"),
    ],
)
def test_rejects_nested_contract_violations(envelope, section, key, value):
    envelope[section][key] = value
    with pytest.raises(BootstrapError):
        read_envelope(io.BytesIO(json.dumps(envelope).encode()))


@pytest.mark.parametrize(
    "payload",
    [b"", b"{}", b"\xff", b"null", b"[]", b"x" * 65537],
    ids=["empty", "missing-fields", "invalid-utf8", "null", "array", "oversized"],
)
def test_rejects_missing_invalid_or_oversize_input(payload):
    with pytest.raises(BootstrapError):
        read_envelope(io.BytesIO(payload))


def test_duplicate_json_keys_and_equal_credentials_are_rejected(envelope):
    value = json.dumps(envelope).replace(
        '"schemaVersion": 2', '"schemaVersion": 2, "schemaVersion": 2'
    )
    with pytest.raises(BootstrapError):
        read_envelope(io.BytesIO(value.encode()))
    changed = copy.deepcopy(envelope)
    changed["configuration"]["dlubal-api-key"] = changed["bearerToken"]
    with pytest.raises(BootstrapError):
        read_envelope(io.BytesIO(json.dumps(changed).encode()))
