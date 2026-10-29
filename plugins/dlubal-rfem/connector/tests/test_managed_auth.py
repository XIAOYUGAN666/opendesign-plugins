import httpx
import pytest
from fastmcp import FastMCP

from dlubal_rfem_mcp.core.auth import GenerationTokenVerifier, LoopbackRequestGuard


@pytest.mark.asyncio
async def test_generation_verifier_accepts_only_exact_bearer():
    verifier = GenerationTokenVerifier("fixture-" + "a" * 40, "ci_" + "1" * 32, 7)
    assert await verifier.verify_token("wrong") is None
    assert await verifier.verify_token("") is None
    verified = await verifier.verify_token("fixture-" + "a" * 40)
    assert verified.client_id == "ci_" + "1" * 32 + ":7"


@pytest.mark.asyncio
async def test_http_auth_and_loopback_guard_reject_before_dispatch():
    from starlette.middleware import Middleware

    mcp = FastMCP(
        "auth-probe",
        auth=GenerationTokenVerifier("fixture-" + "a" * 40, "ci_" + "1" * 32, 1),
    )
    app = mcp.http_app(
        path="/mcp",
        transport="streamable-http",
        stateless_http=True,
        middleware=[Middleware(LoopbackRequestGuard, port=8765)],
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
        ) as client,
    ):
        for token in (None, "wrong"):
            headers = {} if token is None else {"Authorization": "Bearer " + token}
            response = await client.post("/mcp", headers=headers, json={})
            assert response.status_code == 401
        good = {"Authorization": "Bearer fixture-" + "a" * 40}
        for extra in (
            {"Host": "remote.invalid"},
            {"Origin": "https://remote.invalid"},
            {"Origin": "null"},
            {"Host": "user@127.0.0.1:8765"},
        ):
            response = await client.post("/mcp", headers=good | extra, json={})
            assert response.status_code == 403
        for path in (
            "/mcp/",
            "/health",
            "/control",
            "/mcp/sse",
            "/mcp?token=value",
        ):
            response = await client.post(path, headers=good, json={})
            assert response.status_code in (400, 404)
            assert "location" not in response.headers
