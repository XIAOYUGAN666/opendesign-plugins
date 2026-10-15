import asyncio
import json
import socket
import sys

import pytest


async def spawn(envelope):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        envelope["bind"]["port"] = probe.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-s",
        "-m",
        "dlubal_rfem_mcp.managed_bootstrap",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    process.stdin.write(json.dumps(envelope).encode())
    await process.stdin.drain()
    process.stdin.close()
    return process


@pytest.mark.asyncio
async def test_second_provider_cannot_own_same_connector_database(envelope):
    first = await spawn(envelope)
    second = None
    try:
        assert (
            json.loads(await asyncio.wait_for(first.stdout.readline(), 20))["kind"]
            == "ready"
        )
        second = await spawn(envelope)
        assert await asyncio.wait_for(second.stdout.readline(), 20) == b""
        assert await asyncio.wait_for(second.wait(), 10) == 1
    finally:
        for process in (second, first):
            if process is not None:
                if process.returncode is None:
                    process.terminate()
                await asyncio.wait_for(process.wait(), 10)
