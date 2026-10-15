"""First-frame authentication and generation identity checks."""

from __future__ import annotations

import pytest

from rhino_mcp.bridge import BridgeClient, BridgeRejected
from tests.fixtures.bridge_contract import (
    CONTRACT_DIGEST,
    GENERATION,
    PROVIDER_BUILD_ID,
    TOKEN,
    FakeBridge,
    bridge_config,
)


def test_authenticate_is_first_frame_and_once_per_authenticated_socket() -> None:
    with FakeBridge() as fake:
        client = BridgeClient(bridge_config(fake.endpoint))

        assert client.call("gh_clear_canvas", {}) == {}
        assert client.call("gh_clear_canvas", {}) == {}

        assert fake.wait_for_connection()
        assert fake.connection_count == 1
        assert fake.authentication_frames == [
            {
                "type": "authenticate",
                "generation": GENERATION,
                "token": TOKEN,
                "providerBuildId": PROVIDER_BUILD_ID,
                "contractDigest": CONTRACT_DIGEST,
            }
        ]
        assert [command["type"] for command in fake.command_frames] == [
            "gh_clear_canvas",
            "gh_clear_canvas",
        ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("token", "wrong-token-value-0123456789abcdef"),
        ("generation", 2),
        ("provider_build_id", "sha256:" + "1" * 64),
        ("contract_digest", "sha256:" + "0" * 64),
    ],
)
def test_mismatched_authentication_identity_closes_without_command(
    field: str, value: str
) -> None:
    with FakeBridge() as fake:
        config = bridge_config(fake.endpoint, **{field: value})
        client = BridgeClient(config)

        with pytest.raises(BridgeRejected):
            client.call("gh_clear_canvas", {})

        assert fake.wait_for_connection()
        assert fake.connection_count == 1
        assert len(fake.authentication_frames) == 1
        assert fake.command_frames == []


def test_authentication_rejection_prevents_any_command_frame() -> None:
    with FakeBridge(reject_authentication=True) as fake:
        client = BridgeClient(bridge_config(fake.endpoint))

        with pytest.raises(BridgeRejected):
            client.call("gh_clear_canvas", {})

        assert fake.wait_for_connection()
        assert fake.authentication_frames[0]["type"] == "authenticate"
        assert fake.command_frames == []
