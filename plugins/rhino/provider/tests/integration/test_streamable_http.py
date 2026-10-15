"""Exercise the managed MCP server over an actual authenticated loopback socket."""

from __future__ import annotations

import asyncio
import socket

import httpx2
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from rhino_mcp.bridge import BridgeClient
from rhino_mcp.server import (
    ManagedTokenVerifier,
    create_authenticated_http_app,
    create_mcp_server,
)
from tests.fixtures.bridge_contract import FakeBridge, bridge_config


PROVIDER_TOKEN = "test-provider-token-0123456789abcdef"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


async def _wait_started(server: uvicorn.Server, task: asyncio.Task[None]) -> None:
    deadline = asyncio.get_running_loop().time() + 10
    while not server.started:
        if task.done():
            await task
            raise AssertionError("Uvicorn exited before becoming ready.")
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("Uvicorn did not become ready in time.")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_streamable_http_auth_host_and_catalog_over_loopback() -> None:
    with FakeBridge() as bridge:
        port = _free_port()
        verifier = ManagedTokenVerifier(
            PROVIDER_TOKEN,
            client_id="ci_0123456789abcdef0123456789abcdef",
        )
        provider = create_mcp_server(BridgeClient(bridge_config(bridge.endpoint)))
        app = create_authenticated_http_app(
            provider,
            token_verifier=verifier,
            port=port,
        )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_config=None,
                access_log=False,
                log_level="critical",
                proxy_headers=False,
                lifespan="on",
            )
        )
        server_task = asyncio.create_task(server.serve())
        try:
            await _wait_started(server, server_task)
            url = f"http://127.0.0.1:{port}/mcp"
            async with httpx2.AsyncClient(timeout=5, trust_env=False) as client:
                for headers in (
                    {},
                    {"Authorization": f"Basic {PROVIDER_TOKEN}"},
                    {"Authorization": "Bearer wrong-token-value-0123456789"},
                ):
                    response = await client.get(url, headers=headers)
                    assert response.status_code == 401

                response = await client.get(f"{url}?access_token={PROVIDER_TOKEN}")
                assert response.status_code == 401

                response = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {PROVIDER_TOKEN}",
                        "Host": f"localhost:{port}",
                    },
                )
                assert response.status_code == 421, response.text

                response = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {PROVIDER_TOKEN}",
                        "Origin": f"http://localhost:{port}",
                    },
                )
                assert response.status_code == 403, response.text

                response = await client.get(
                    f"http://127.0.0.1:{port}/health",
                    headers={"Authorization": f"Bearer {PROVIDER_TOKEN}"},
                )
                assert response.status_code == 404

            async with httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {PROVIDER_TOKEN}"},
                timeout=10,
                trust_env=False,
            ) as http_client:
                async with streamable_http_client(
                    url,
                    http_client=http_client,
                ) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as client:
                        await client.initialize()
                        assert len((await client.list_tools()).tools) == 70
                        assert len((await client.list_resources()).resources) == 1
                        assert (
                            len(
                                (
                                    await client.list_resource_templates()
                                ).resource_templates
                            )
                            == 3
                        )
                        assert len((await client.list_prompts()).prompts) == 2
        finally:
            server.should_exit = True
            await asyncio.wait_for(
                asyncio.gather(server_task, return_exceptions=True), timeout=10
            )
            verifier.clear()
