"""P03 probes; lifecycle projection is test-only, not a persistent journal."""

import asyncio
import hashlib
import io
import json
import threading
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from rhino_mcp.bridge import BridgeClient, BridgeResultUnknown
from rhino_mcp.managed_bootstrap import (
    StartupConfigurationError,
    read_connector_envelope,
)
from rhino_mcp.server import ManagedTokenVerifier, create_mcp_server
from tests.fixtures.bridge_contract import FakeBridge, bridge_config
from tests.fixtures.mcp_client import connected_client
from tests.connector.test_managed_bootstrap import _payload


class AttemptObservation:
    """Minimal future Connector-owned expression, never inferred from inFlight=0."""

    def __init__(self):
        self.in_flight = 0
        self.unknown = False

    def observe_failure(self, error):
        self.in_flight = 0
        self.unknown |= isinstance(error, BridgeResultUnknown)

    @property
    def state(self):
        return "unknown" if self.unknown else "running" if self.in_flight else "idle"


def test_unknown_write_is_not_settled():
    observation = AttemptObservation()
    observation.in_flight = 1
    with FakeBridge(close_after_command=True) as fake:
        bridge = BridgeClient(bridge_config(fake.endpoint))
        try:
            bridge.call("delete_object", {"all": True})
            pytest.fail("lost response was treated as successful")
        except BridgeResultUnknown as error:
            assert error.category == "resultUnknown"
            observation.observe_failure(error)
        finally:
            bridge.disconnect()
        assert len(fake.command_frames) == 1
        assert fake.command_frames[0]["type"] == "delete_object"
    assert observation.in_flight == 0
    assert observation.state == "unknown"
    # This latch is NOT a current durable Rhino execution status API. T30 must
    # add the Connector-owned lifecycle expression; no maintenance permit exists.


@pytest.mark.asyncio
async def test_tool_declarations_need_no_engineering_connection(monkeypatch):
    dispatches = []

    def forbidden(self, *args, **kwargs):
        dispatches.append(args)
        pytest.fail("metadata connected/dispatched to Bridge")

    monkeypatch.setattr(BridgeClient, "connect", forbidden)
    monkeypatch.setattr(BridgeClient, "call", forbidden)
    expected = json.loads(
        (Path(__file__).parents[1] / "fixtures/expected_mcp_catalog.json").read_text(
            encoding="utf-8"
        )
    )["tools"]
    async with connected_client(create_mcp_server()) as client:
        tools = (await client.list_tools()).tools
        assert len(tools) == 70
        actual = sorted(
            [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                    "annotations": t.annotations.model_dump(
                        by_alias=True, exclude_none=False
                    )
                    if t.annotations
                    else None,
                }
                for t in tools
            ],
            key=lambda t: t["name"],
        )

        def digest(value):
            return hashlib.sha256(
                json.dumps(
                    value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest()

        assert digest(actual) == digest(expected)
        resources = (await client.list_resources()).resources
        templates = (await client.list_resource_templates()).resource_templates
        prompts = (await client.list_prompts()).prompts
        assert len(resources) == 1
        assert len(templates) == 3
        assert len(prompts) == 2
        assert str(resources[0].uri) == "rhinoscript://modules"
        assert {t.uri_template for t in templates} == {
            "rhinoscript://module/{module_name}",
            "rhinoscript://function/{function_name}",
            "rhino://guidance/{topic}",
        }
        assert {p.name for p in prompts} == {
            "asset_general_strategy",
            "rhinoscript_workflow",
        }
        resource = await client.read_resource("rhinoscript://modules")
        assert resource.contents
    assert dispatches == []


@pytest.mark.asyncio
async def test_missing_business_key_does_not_become_mcp_bearer(monkeypatch):
    """Rhino's engineering credential is its Bridge token, not a Dlubal Key."""
    dispatches = []
    monkeypatch.setattr(BridgeClient, "call", lambda *a, **k: dispatches.append(a))
    async with connected_client(create_mcp_server()) as client:
        result = await client.call_tool("delete_object", {"all": True})
        assert result.is_error
    assert dispatches == []
    payload = _payload()
    verifier = ManagedTokenVerifier(
        payload["bearerToken"], client_id=payload["binding"]["connectorInstanceId"]
    )
    assert await verifier.verify_token(payload["bearerToken"]) is not None
    assert await verifier.verify_token("test-bridge-token-0123456789abcdef") is None
    context = read_connector_envelope(io.BytesIO(json.dumps(_payload()).encode()))
    assert context.nativeContext is None
    payload = _payload()
    payload["oldConnectionState"] = "connected"
    with pytest.raises(StartupConfigurationError):
        read_connector_envelope(io.BytesIO(json.dumps(payload).encode()))


@pytest.mark.asyncio
async def test_background_execution_is_busy_after_call_returns(monkeypatch):
    """MCP wait expiration cannot settle the synchronous in-flight Bridge call."""
    entered, release = threading.Event(), threading.Event()
    observation = AttemptObservation()
    errors = []

    def respond(command):
        if command["type"] == "describe_capabilities":
            return {
                "status": "success",
                "result": {
                    "version": "0.1.2",
                    "command_count": 2,
                    "commands": [
                        {
                            "name": "describe_capabilities",
                            "read_only": True,
                            "supports_dry_run": False,
                        },
                        {
                            "name": "delete_object",
                            "read_only": False,
                            "supports_dry_run": False,
                        },
                    ],
                    "perception": {
                        "description": "Synthetic probe",
                        "envelope_flags": [],
                    },
                },
            }
        entered.set()
        assert release.wait(3)
        return {
            "status": "success",
            "result": {"deleted": True, "count": 0, "scope": "all"},
        }

    with FakeBridge(response=respond) as fake:
        bridge = BridgeClient(bridge_config(fake.endpoint))

        original_call = bridge.call

        def dispatch(*args, **kwargs):
            observation.in_flight = 1
            try:
                return original_call(*args, **kwargs)
            except Exception as error:
                errors.append(error)
                observation.observe_failure(error)
                raise
            finally:
                observation.in_flight = 0

        monkeypatch.setattr(bridge, "call", dispatch)
        async with connected_client(create_mcp_server(bridge)) as client:
            task = asyncio.create_task(client.call_tool("delete_object", {"all": True}))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                with pytest.raises(TimeoutError):
                    async with asyncio.timeout(0.01):
                        await asyncio.shield(task)
                assert not task.done() and observation.state == "running"
                assert [frame["type"] for frame in fake.command_frames] == [
                    "describe_capabilities",
                    "delete_object",
                ]
            finally:
                release.set()
                reply = await asyncio.wait_for(task, 2)
            assert not reply.is_error and errors == []
        assert observation.state == "idle"


class StatusFrame(BaseModel):
    """Test-only stdout framing feasibility, fed by observed typed Bridge errors."""

    model_config = ConfigDict(extra="forbid")
    sequence: int = Field(strict=True, ge=1)
    state: Literal["idle", "running", "unknown"]


class StatusReader:
    def __init__(self):
        self.sequence = 0
        self.state = "unknown"

    def accept(self, frame):
        if frame.sequence <= self.sequence:
            return False
        self.sequence, self.state = frame.sequence, frame.state
        return True

    def eof(self):
        self.state = "unknown"


def test_ready_then_status_sequence_and_eof_do_not_invent_idle():
    observation = AttemptObservation()
    observation.observe_failure(BridgeResultUnknown("synthetic post-dispatch loss"))
    stream = io.StringIO()
    stream.write(json.dumps({"kind": "ready"}) + "\n")
    stream.write(
        StatusFrame(sequence=2, state=observation.state).model_dump_json() + "\n"
    )
    ready, status = stream.getvalue().splitlines()
    assert json.loads(ready) == {"kind": "ready"}
    reader = StatusReader()
    assert reader.accept(StatusFrame.model_validate_json(status))
    assert not reader.accept(StatusFrame(sequence=2, state="idle"))
    assert not reader.accept(StatusFrame(sequence=1, state="idle"))
    assert reader.state == "unknown"
    assert reader.accept(StatusFrame(sequence=3, state="idle"))
    reader.eof()
    assert reader.state == "unknown"
