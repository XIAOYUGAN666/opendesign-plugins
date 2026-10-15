"""Managed startup validates exact V2 identities without exposing credentials."""

import copy
import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest

from rhino_mcp.build_identity import PROVIDER_BUILD_ID
from rhino_mcp.managed_bootstrap import (
    MAX_STARTUP_BYTES,
    StartupConfigurationError,
    parse_startup_envelope,
    read_startup_envelope,
)
from tests.provider.test_metadata_startup import metadata_payload

CONTRACT_ROOT = Path(__file__).resolve().parents[3] / "contracts"
PROVIDER_TOKEN = "test-provider-token-0123456789abcdef"
BRIDGE_TOKEN = "test-bridge-token-0123456789abcdef"


def _payload():
    value = metadata_payload()
    value.update(
        mode="execution",
        bearerToken=PROVIDER_TOKEN,
        configuration={},
        data={"stateDirectory": "C:\\provider-state\\rhino"},
        software={"safeIdentity": "si_rhino", "build": "8.26.25349.19001"},
        nativeContext={
            "host": "127.0.0.1",
            "port": 50123,
            "token": BRIDGE_TOKEN,
            "generation": 1,
            "providerBuildId": PROVIDER_BUILD_ID,
            "contractDigest": "sha256:"
            + hashlib.sha256(
                (CONTRACT_ROOT / "protocol.json").read_bytes()
            ).hexdigest(),
        },
    )
    return value


def _parse(payload=None):
    return parse_startup_envelope(
        json.dumps(_payload() if payload is None else payload).encode()
    )


def test_valid_startup_envelope_resolves_exact_managed_identities():
    envelope = _parse()
    assert envelope.schemaVersion == 2
    assert envelope.mode == "execution"
    assert envelope.generation == 1
    assert envelope.providerBuildId == PROVIDER_BUILD_ID
    assert envelope.contract_root == CONTRACT_ROOT
    assert envelope.nativeContext.token.get_secret_value() == BRIDGE_TOKEN
    assert BRIDGE_TOKEN not in repr(envelope)


@pytest.mark.parametrize(
    "path,value",
    [
        (("schemaVersion",), 1),
        (("schemaVersion",), True),
        (("providerBuildId",), "sha256:" + "1" * 64),
        (("connectorInstanceId",), "unsafe"),
        (("generation",), 0),
        (("generation",), True),
        (("generation",), 2**53),
        (("nativeContext", "providerBuildId"), "sha256:" + "1" * 64),
        (("nativeContext", "contractDigest"), "sha256:" + "1" * 64),
        (("software", "build"), "8.27"),
        (("data", "stateDirectory"), "relative"),
        (("data", "stateDirectory"), "C:\\state\\..\\other"),
        (("data", "stateDirectory"), "\\\\server\\share"),
        (("configuration", "bridgeToken"), BRIDGE_TOKEN),
        (("nativeContext", "token"), PROVIDER_TOKEN),
        (("nativeContext", "port"), 49152),
        (("bearerToken",), "short"),
    ],
)
def test_invalid_identity_or_secret_domain_is_rejected_safely(path, value):
    payload = _payload()
    cursor = payload
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(StartupConfigurationError) as caught:
        _parse(payload)
    assert PROVIDER_TOKEN not in str(caught.value)
    assert BRIDGE_TOKEN not in str(caught.value)


@pytest.mark.parametrize("section", ["bind", "nativeContext"])
@pytest.mark.parametrize(
    "key,value",
    [
        ("host", "localhost"),
        ("host", "127.0.0.2"),
        ("host", "::1"),
        ("port", 1023),
        ("port", 65536),
        ("port", True),
        ("unexpected", True),
    ],
)
def test_listener_configuration_is_strict_loopback_and_bounded(section, key, value):
    payload = _payload()
    payload[section][key] = value
    with pytest.raises(StartupConfigurationError):
        _parse(payload)


@pytest.mark.parametrize(
    "field", ["configuration", "data", "software", "nativeContext"]
)
def test_execution_requires_native_and_generic_context(field):
    payload = _payload()
    payload.pop(field)
    with pytest.raises(StartupConfigurationError):
        _parse(payload)


@pytest.mark.parametrize(
    "field", ["configuration", "data", "software", "nativeContext"]
)
def test_metadata_rejects_engineering_context(field):
    payload = metadata_payload()
    payload[field] = copy.deepcopy(_payload()[field])
    with pytest.raises(StartupConfigurationError):
        _parse(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("contractRoot", str(CONTRACT_ROOT)),
        ("bridge", {}),
        ("requestTimeoutMs", 15000),
        ("packageId", "rhino"),
    ],
)
def test_unknown_legacy_fields_are_rejected(field, value):
    payload = _payload()
    payload[field] = value
    with pytest.raises(StartupConfigurationError):
        _parse(payload)


def test_duplicate_members_non_json_constants_and_invalid_utf8_are_rejected():
    valid = json.dumps(_payload(), separators=(",", ":"))
    for raw in (
        valid.replace(
            '"schemaVersion":2', '"schemaVersion":2,"schemaVersion":2'
        ).encode(),
        valid.replace('"schemaVersion":2', '"schemaVersion":NaN').encode(),
        b"\xff",
    ):
        with pytest.raises(StartupConfigurationError):
            parse_startup_envelope(raw)


def test_stdin_is_bounded_and_requires_one_complete_json_document():
    encoded = json.dumps(_payload()).encode()
    exact_limit = encoded + b" " * (MAX_STARTUP_BYTES - len(encoded))
    assert read_startup_envelope(BytesIO(exact_limit)).generation == 1
    for raw in (exact_limit + b" ", b"{} {}"):
        with pytest.raises(StartupConfigurationError):
            read_startup_envelope(BytesIO(raw))
