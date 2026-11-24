"""A tool waiting on a Dlubal application must never silence the server.

Before 1.0.23 every tool ran on the event loop. One call RFEM never answered
froze /health, /auth-check and /control with it, and the Connector, which
needs /control to drain a server before restarting it, could not recover.
"""

import inspect
import threading
import time

import pytest
from fastmcp import FastMCP
from fastmcp.tools.tool import FunctionTool
from starlette.testclient import TestClient

from dlubal_mcp.core import server
from dlubal_mcp.core.config import DlubalMCPSettings
from dlubal_mcp.core.off_loop import off_event_loop
from dlubal_mcp.core.operation_control import OperationControl
from dlubal_mcp.core.products import ProductRegistry

TOKEN = "event-loop-test-" + "a" * 48
AUTH = {"Authorization": "Bearer " + TOKEN}
WAIT = 10


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    for name in (
        "ALLOWED_API_KEYS",
        "DLUBAL_MCP_ALLOWED_API_KEYS",
        "DLUBAL_MCP_HOST",
        "DLUBAL_MCP_REQUIRE_AUTH",
        "DLUBAL_MCP_PRODUCTS",
    ):
        monkeypatch.delenv(name, raising=False)
    for product in ("RFEM", "RSTAB", "RSECTION"):
        monkeypatch.setenv(
            f"{product}_EXECUTION_STATE_FILE", str(tmp_path / f"{product}.sqlite3")
        )


def _server_with(monkeypatch, tool):
    def build(settings, registry):
        mcp = FastMCP("event-loop test")
        mcp.tool(tool, name="wait_on_application")
        return mcp

    monkeypatch.setattr(server, "_build_mcp", build)
    return server.create_asgi_app(DlubalMCPSettings(allowed_api_keys=TOKEN))


def _session(client):
    headers = {**AUTH, "Accept": "application/json, text/event-stream"}
    initialized = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "event-loop-test", "version": "1"},
            },
        },
    )
    assert initialized.status_code == 200
    if session := initialized.headers.get("mcp-session-id"):
        headers["Mcp-Session-Id"] = session
    client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    return headers


def _in_background(fn):
    result = {}
    thread = threading.Thread(target=lambda: result.update(value=fn()), daemon=True)
    thread.start()
    return thread, result


def _call_tool(client, headers):
    return client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "wait_on_application", "arguments": {}},
        },
    ).text


def _hanging_tool(entered, release):
    def wait_on_application() -> str:
        entered.set()
        release.wait(WAIT)
        return "application answered"

    return wait_on_application


def test_server_answers_while_a_tool_waits_on_the_application(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    app = _server_with(monkeypatch, off_event_loop(_hanging_tool(entered, release)))
    with TestClient(app) as client:
        headers = _session(client)
        caller, called = _in_background(lambda: _call_tool(client, headers))
        try:
            assert entered.wait(WAIT)
            started = time.monotonic()
            assert client.get("/health").status_code == 200
            assert client.get("/auth-check", headers=AUTH).status_code == 200
            assert client.get("/control", headers=AUTH).json()["state"] == "idle"
            assert time.monotonic() - started < 2
        finally:
            release.set()
        caller.join(WAIT)
    assert "application answered" in called["value"]


def test_a_tool_on_the_event_loop_silences_the_server(monkeypatch):
    # The failure the test above guards against, reproduced on purpose: proof
    # that this harness can see a frozen loop at all.
    entered, release = threading.Event(), threading.Event()
    app = _server_with(monkeypatch, _hanging_tool(entered, release))
    with TestClient(app) as client:
        headers = _session(client)
        caller, _ = _in_background(lambda: _call_tool(client, headers))
        try:
            assert entered.wait(WAIT)
            probe, answered = _in_background(
                lambda: client.get("/auth-check", headers=AUTH).status_code
            )
            probe.join(1)
            assert "value" not in answered
        finally:
            release.set()
        caller.join(WAIT)
        probe.join(WAIT)
    assert answered["value"] == 200


@pytest.mark.asyncio
async def test_every_tool_runs_off_the_event_loop_with_unchanged_schemas():
    settings = DlubalMCPSettings(allowed_api_keys=TOKEN, allow_scripts=True)
    mcp = server._build_mcp(settings, ProductRegistry(OperationControl()))
    tools = await mcp.get_tools()
    assert {"get_rfem_application_info", "run_rfem_script", "health_check"} <= set(
        tools
    )
    for name, tool in tools.items():
        assert inspect.iscoroutinefunction(tool.fn), f"{name} runs on the event loop"
        plain = FunctionTool.from_function(tool.fn.__wrapped__, name=name)
        assert tool.parameters == plain.parameters, name
        assert tool.output_schema == plain.output_schema, name
