"""Run a real MCP ClientSession against the Connector over in-memory streams."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.server.mcpserver import MCPServer
from mcp.shared.memory import create_client_server_memory_streams


@asynccontextmanager
async def connected_client(server: MCPServer) -> AsyncIterator[ClientSession]:
    async with create_client_server_memory_streams() as (
        client_streams,
        server_streams,
    ):
        server_task = asyncio.create_task(
            server._lowlevel_server.run(
                *server_streams,
                server._lowlevel_server.create_initialization_options(),
            )
        )
        try:
            async with ClientSession(*client_streams) as client:
                await client.initialize()
                yield client
        finally:
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)
