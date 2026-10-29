"""The managed MCP bearer is scoped to one app-issued connector identity."""

from __future__ import annotations

import pytest

from rhino_mcp.server import ManagedTokenVerifier


CONNECTOR_TOKEN = "test-connector-token-0123456789abcdef"


@pytest.mark.asyncio
async def test_managed_token_verifier_accepts_only_the_exact_generation_token() -> None:
    verifier = ManagedTokenVerifier(
        CONNECTOR_TOKEN,
        client_id="ci_0123456789abcdef0123456789abcdef",
    )

    accepted = await verifier.verify_token(CONNECTOR_TOKEN)
    assert accepted is not None
    assert accepted.client_id == "ci_0123456789abcdef0123456789abcdef"
    assert accepted.scopes == ["rhino:invoke"]
    assert await verifier.verify_token("x" + CONNECTOR_TOKEN[1:]) is None
    assert await verifier.verify_token("") is None

    verifier.clear()
    assert await verifier.verify_token(CONNECTOR_TOKEN) is None
