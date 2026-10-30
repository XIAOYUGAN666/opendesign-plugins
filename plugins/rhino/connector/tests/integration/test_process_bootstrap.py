"""The managed module consumes one bounded stdin envelope and reports only readiness."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from rhino_mcp.build_identity import CONNECTOR_BUILD_ID
from rhino_mcp.managed_bootstrap import MAX_STARTUP_BYTES
from tests.fixtures.bridge_contract import FakeBridge


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = PROJECT_ROOT.parent / "contracts"
CONNECTOR_TOKEN = "test-connector-token-0123456789abcdef"
BRIDGE_TOKEN = "test-bridge-token-0123456789abcdef"


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _environment_without_plugin_overrides() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("RHINO_MCP_", "UVICORN_"))
    }


def _start_process(payload: bytes) -> subprocess.Popen[bytes]:
    options: dict[str, object] = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(
        [sys.executable, "-I", "-s", "-m", "rhino_mcp.managed_bootstrap"],
        cwd=PROJECT_ROOT,
        env=_environment_without_plugin_overrides(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **options,
    )
    assert process.stdin is not None
    process.stdin.write(payload + b"\n")
    process.stdin.flush()
    return process


def _envelope(
    bridge_port: int, mcp_port: int, mode: str = "execution", generation: int = 1
) -> bytes:
    value = {
        "schemaVersion": 3,
        "binding": {
            "packageId": "rhino",
            "releaseDigest": "sha256:" + "a" * 64,
            "connectorInstanceId": "ci_0123456789abcdef0123456789abcdef",
            "generation": generation,
            "configurationRevision": 1,
            "secretRevision": 0,
        },
        "mode": "runtime" if mode == "execution" else mode,
        "bind": {"host": "127.0.0.1", "port": mcp_port, "path": "/mcp"},
        "bearerToken": CONNECTOR_TOKEN,
        "connectorBuildId": CONNECTOR_BUILD_ID,
        "configuration": {},
        "secrets": [],
        "dataDirectory": None,
        "nativePeer": None,
    }
    if mode == "execution":
        value["nativePeer"] = {
                "host": "127.0.0.1",
                "port": bridge_port,
                "token": BRIDGE_TOKEN,
                "generation": 1,
                "connectorBuildId": CONNECTOR_BUILD_ID,
                "contractDigest": "sha256:"
                + hashlib.sha256(
                    (CONTRACT_ROOT / "protocol.json").read_bytes()
                ).hexdigest(),
            "softwareInstanceId": "si_rhino",
        }
    return json.dumps(value).encode("utf-8")


def _read_ready_line(process: subprocess.Popen[bytes], timeout: float = 15) -> bytes:
    assert process.stdout is not None
    ready: queue.Queue[bytes] = queue.Queue()
    reader = threading.Thread(
        target=lambda: ready.put(process.stdout.readline()),
        daemon=True,
    )
    reader.start()
    return ready.get(timeout=timeout)


def _stop_process(process: subprocess.Popen[bytes]) -> tuple[bytes, bytes, bool]:
    if process.poll() is None:
        if sys.platform == "win32":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
    try:
        # The bootstrap itself permits up to ten seconds for its server cleanup.
        # Observe that owned graceful path before classifying it as force-killed.
        stdout, stderr = process.communicate(timeout=15)
        return stdout, stderr, True
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
        return stdout, stderr, False


@pytest.mark.skipif(
    sys.platform != "win32", reason="managed process flags are Windows-native"
)
@pytest.mark.parametrize(
    "mode,generation", [("metadata", 1), ("execution", 1), ("execution", 2)]
)
def test_process_bootstrap_reads_one_envelope_and_emits_one_safe_ready_record(
    mode: str,
    generation: int,
) -> None:
    with FakeBridge() as bridge:
        process = _start_process(
            _envelope(bridge.endpoint[1], _free_port(), mode, generation)
        )
        try:
            line = _read_ready_line(process)
            ready = json.loads(line)
            assert ready == {
                "schemaVersion": 3,
                "kind": "ready",
                "binding": json.loads(_envelope(bridge.endpoint[1], ready["port"], mode, generation))["binding"],
                "connectorBuildId": CONNECTOR_BUILD_ID,
                "port": ready["port"],
            }
            assert ready["port"] >= 1024
            assert CONNECTOR_TOKEN.encode() not in line
            assert BRIDGE_TOKEN.encode() not in line
            status_line = _read_ready_line(process)
            assert bridge.connection_count == 0
            import httpx

            response = httpx.post(
                f"http://127.0.0.1:{ready['port']}/mcp",
                headers={"Authorization": "Bearer " + BRIDGE_TOKEN},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                timeout=5,
            )
            assert response.status_code == 401
        finally:
            remaining_stdout, stderr, stopped_without_force_kill = _stop_process(
                process
            )

        assert stopped_without_force_kill, (
            "managed connector ignored its shutdown signal"
        )
        assert process.returncode is not None
        statuses = [
            json.loads(frame) for frame in (status_line + remaining_stdout).splitlines()
        ]
        previous = 0
        for status in statuses:
            assert set(status) == {
                "schemaVersion",
                "kind",
                "connectorInstanceId",
                "generation",
                "sequence",
                "state",
                "inFlight",
            }
            assert status["schemaVersion"] == 1
            assert status["kind"] == "status"
            assert status["connectorInstanceId"] == ready["binding"]["connectorInstanceId"]
            assert status["generation"] == ready["binding"]["generation"]
            assert status["sequence"] > previous
            assert status["state"] == "idle"
            assert status["inFlight"] == 0
            previous = status["sequence"]
        assert CONNECTOR_TOKEN.encode() not in status_line + remaining_stdout
        assert BRIDGE_TOKEN.encode() not in status_line + remaining_stdout
        assert CONNECTOR_TOKEN.encode() not in stderr
        assert BRIDGE_TOKEN.encode() not in stderr


@pytest.mark.skipif(
    sys.platform != "win32", reason="managed process flags are Windows-native"
)
def test_oversized_process_envelope_fails_before_any_listener_is_opened() -> None:
    process = _start_process(b" " * (MAX_STARTUP_BYTES + 1))
    stdout, stderr = process.communicate(timeout=15)

    assert process.returncode != 0
    assert stdout == b""
    assert b"invalidStartup" in stderr
    assert CONNECTOR_TOKEN.encode() not in stderr
    assert BRIDGE_TOKEN.encode() not in stderr


@pytest.mark.skipif(
    sys.platform != "win32", reason="managed process flags are Windows-native"
)
def test_connector_resource_generation_two_authenticates_bridge_lease_one():
    import asyncio
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def discover(port):
        async with httpx2.AsyncClient(
            headers={"Authorization": "Bearer " + CONNECTOR_TOKEN},
            timeout=10,
            trust_env=False,
        ) as http_client:
            async with streamable_http_client(
                f"http://127.0.0.1:{port}/mcp",
                http_client=http_client,
            ) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    result = await client.call_tool("describe_capabilities", {})
                    assert not result.is_error

    def respond(command):
        assert command["type"] == "describe_capabilities"
        return {
            "status": "success",
            "result": {
                "version": "0.1.2",
                "command_count": 1,
                "commands": [{"name": "describe_capabilities", "read_only": True}],
                "perception": {"description": "fixture", "envelope_flags": []},
            },
        }

    with FakeBridge(response=respond) as bridge:
        process = _start_process(
            _envelope(bridge.endpoint[1], _free_port(), "execution", 2)
        )
        try:
            ready = json.loads(_read_ready_line(process))
            status = json.loads(_read_ready_line(process))
            assert ready["binding"]["generation"] == 2
            assert ready["kind"] == "ready" and ready["schemaVersion"] == 3
            assert status["generation"] == 2 and status["kind"] == "status"
            asyncio.run(discover(ready["port"]))
            assert len(bridge.authentication_frames) == 1
            assert bridge.authentication_frames[0]["generation"] == 1
            assert bridge.authentication_frames[0]["token"] == BRIDGE_TOKEN
            assert bridge.authentication_frames[0]["token"] != CONNECTOR_TOKEN
            assert [frame["type"] for frame in bridge.command_frames] == [
                "describe_capabilities"
            ]
        finally:
            remaining, stderr, graceful = _stop_process(process)
        assert graceful and process.returncode is not None
        assert CONNECTOR_TOKEN.encode() not in remaining + stderr
        assert BRIDGE_TOKEN.encode() not in remaining + stderr
