"""P03 feasibility only: lazy declarations are not managed-startup acceptance."""

import asyncio
import io
import json
import threading
import time
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

import grpc
import pytest
from fastmcp import Client, FastMCP
from pydantic import BaseModel, ConfigDict, Field

from dlubal_rfem_mcp.core import server
from dlubal_rfem_mcp.core.auth import GenerationTokenVerifier
from dlubal_rfem_mcp.core.catalog import catalog_digest
from dlubal_rfem_mcp.core.operation_control import OperationControl
from dlubal_rfem_mcp.managed_bootstrap import BootstrapError, read_connector_envelope
from dlubal_rfem_mcp.rfem.execution import ExecutionManager, ExecutionUncertain
from dlubal_rfem_mcp.rfem.session import RFEMSession


@pytest.fixture
def lazy_declarations(envelope, monkeypatch):
    """Keep production registration/health while delaying the real session."""
    from dlubal.api.rfem import application

    constructions = []

    def forbidden(**kwargs):
        constructions.append(kwargs)
        raise RuntimeError("synthetic engineering endpoint must not be reached")

    monkeypatch.setattr(application, "Application", forbidden)

    class LazySession:
        @contextmanager
        def connection(self):
            # Test-only proposed declaration mode has no engineering key.
            # Use the real session's constructor gate BEFORE SDK import/dispatch.
            with RFEMSession("", "127.0.0.1", 9000).connection() as app:
                yield app  # pragma: no cover - missing key must reject above

    monkeypatch.setattr(server, "RFEMSession", lambda *a, **k: LazySession())
    return server.build_mcp(
        read_connector_envelope(io.BytesIO(json.dumps(envelope).encode()))
    ), constructions


@pytest.mark.asyncio
async def test_missing_business_key_does_not_become_mcp_bearer(lazy_declarations):
    mcp, constructions = lazy_declarations
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_rfem_application_info", {}, raise_on_error=False
        )
        assert result.is_error
    assert constructions == []


@pytest.mark.asyncio
async def test_tool_declarations_need_no_engineering_connection(
    lazy_declarations, monkeypatch
):
    from dlubal.api.common import connection

    monkeypatch.setattr(
        connection, "_get_config_path", lambda: pytest.fail("metadata read INI")
    )
    mcp, constructions = lazy_declarations
    expected = json.loads(
        (Path(__file__).parent / "fixtures/tool-catalog-v1.json").read_text(
            encoding="utf-8"
        )
    )["tools"]
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == {
            "health_check",
            "get_rfem_application_info",
            "inspect_rfem_api",
            "write_rfem_model",
            "calculate_rfem_model",
            "run_rfem_script",
            "get_rfem_execution",
            "read_rfem_results",
        }
        assert len(tools) == 8
        observed = [tool.model_dump(by_alias=True, exclude_none=True) for tool in tools]
        for tool in [*observed, *expected]:
            if tool["name"] == "get_rfem_application_info":
                tool.pop("outputSchema")
        assert catalog_digest(observed) == catalog_digest(expected)
        assert await client.list_resources() == []
        assert await client.list_prompts() == []
        health = await client.call_tool("health_check", {}, raise_on_error=False)
        assert health.is_error
    assert constructions == []


@pytest.mark.asyncio
async def test_current_managed_bootstrap_still_requires_distinct_manual_key(envelope):
    verifier = GenerationTokenVerifier(
        envelope["bearerToken"], envelope["binding"]["connectorInstanceId"], 1
    )
    assert await verifier.verify_token(envelope["bearerToken"]) is not None
    assert (
        await verifier.verify_token(envelope["secrets"][0]["value"]) is None
    )
    payload = {**envelope, "secrets": []}
    assert read_connector_envelope(io.BytesIO(json.dumps(payload).encode())).configuration is None
    for credential in ("", envelope["bearerToken"]):
        payload = {**envelope, "secrets": [{"fieldId": "dlubal-api-key", "value": credential}]}
        with pytest.raises(BootstrapError, match="invalidStartupEnvelope"):
            read_connector_envelope(io.BytesIO(json.dumps(payload).encode()))


def terminal(manager, operation):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = manager.status(operation)
        if result["state"] != "running":
            return result
        time.sleep(0.01)
    pytest.fail("owned synthetic worker did not settle")


@pytest.mark.asyncio
async def test_background_execution_is_busy_after_call_returns(tmp_path):
    control = OperationControl()
    manager = ExecutionManager(tmp_path / "jobs.db", control=control)
    entered, finish = threading.Event(), threading.Event()
    operation = str(uuid4())

    def work(*_):
        entered.set()
        assert finish.wait(3)

    # Only the wait boundary is shortened; the journal/control are production.
    mcp = FastMCP("execution-observation-probe")

    @mcp.tool()
    def submit_observation() -> dict:
        return manager.submit(
            operation, "probe", "read-only-synthetic", work, wait_seconds=0
        )

    try:
        async with Client(mcp) as client:
            reply = await client.call_tool("submit_observation", {})
        assert entered.wait(1)
        assert reply.structured_content["state"] == "running"
        assert manager.unfinished()[0]["execution_id"] == operation
        assert control.prepare_stop()["state"] == "running"
        assert "lease" not in control.prepare_stop()
    finally:
        finish.set()
    assert terminal(manager, operation)["state"] == "succeeded"
    assert control.prepare_stop()["state"] == "draining"


def test_unknown_write_is_not_settled(tmp_path):
    control = OperationControl()
    manager = ExecutionManager(tmp_path / "jobs.db", control=control)
    operation = str(uuid4())

    def uncertain(*_):
        raise ExecutionUncertain("synthetic post-dispatch connection uncertainty")

    assert manager.submit(operation, "probe", "write", uncertain)["state"] == "unknown"
    assert manager.status(operation)["state"] == "unknown"
    assert control.prepare_stop()["state"] == "unknown"
    assert "lease" not in control.prepare_stop()
    assert (
        manager.submit(
            str(uuid4()), "probe", "new", lambda *_: pytest.fail("replayed")
        )["state"]
        == "busy"
    )


@pytest.fixture
def sdk_rpc(tmp_path, monkeypatch):
    """Unmodified locked Application; replace only external channel/RPC seam."""
    from dlubal.api.common import connection
    from dlubal.api.rfem import application, application_pb2_grpc

    ini = tmp_path / "config.ini"
    ini.write_text("[api_keys]\nfixture = synthetic-business-key\n", encoding="utf-8")
    monkeypatch.setattr(connection, "_get_config_path", lambda: ini)
    calls = []
    entered, release = threading.Event(), threading.Event()
    release.set()
    failure = []

    class Stub:
        def __init__(self, channel):
            assert channel is channel_sentinel

        def get_application_info(self, request, **kwargs):
            calls.append(("appInfo", request, kwargs))
            entered.set()
            assert release.wait(3)
            if failure:
                raise failure[0]
            return SimpleNamespace(
                version="6.14.0013", name="RFEM", full_version="6.14.0013.249.synthetic"
            )

        def close_connection(self, request, **kwargs):
            calls.append(("close", request, kwargs))

    channel_sentinel = object()
    monkeypatch.setattr(application, "init_channel", lambda *a, **k: channel_sentinel)
    monkeypatch.setattr(application_pb2_grpc, "ApplicationStub", Stub)
    return application, calls, entered, release, failure


def test_sdk_constructor_rpc_count_and_expected_build(sdk_rpc):
    application, calls, *_ = sdk_rpc
    assert version("dlubal.api") == "2.14.13"
    app = application.Application(api_key_value="synthetic-business-key")
    assert [call[0] for call in calls] == ["appInfo"]
    app.close_connection()
    calls.clear()
    with RFEMSession(
        "synthetic-business-key", "127.0.0.1", 9000, expected_build="6.14.0013.249"
    ).connection():
        assert [call[0] for call in calls] == ["appInfo", "appInfo"]
    assert [call[0] for call in calls] == ["appInfo", "appInfo", "close"]
    assert all(kwargs == {} for _, _, kwargs in calls)  # no synchronous RPC deadline


@pytest.mark.asyncio
async def test_sdk_constructor_rpc_count_and_verification_timeout(sdk_rpc):
    _, calls, entered, release, _ = sdk_rpc
    release.clear()
    session = RFEMSession(
        "synthetic-business-key", "127.0.0.1", 9000, expected_build="6.14.0013.249"
    )
    control = OperationControl()
    done = threading.Event()
    errors = []

    def verify():
        try:
            with control.reading(), session.connection():
                pass  # constructor + expected-build appInfo are read-only
        except Exception as error:  # noqa: BLE001 - propagate owned thread errors below
            errors.append(error)
        finally:
            done.set()

    worker = threading.Thread(target=verify)
    worker.start()
    try:
        assert entered.wait(1)
        # Test-only response-budget projection consumes the live owner snapshot.
        # The injected elapsed time is 15.001 seconds; no production worker or
        # cancellation mechanism is being claimed by this feasibility probe.
        assert verification_response(control, elapsed=14.999) == "pending"
        assert verification_response(control, elapsed=15.001) == "timeoutBusy"
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.01):
                await asyncio.to_thread(done.wait, 0.05)
        assert worker.is_alive() and not done.is_set()
        assert control.prepare_stop()["state"] == "running"
        assert "lease" not in control.prepare_stop()
        assert [call[0] for call in calls] == ["appInfo"]
        assert calls[0][2] == {}
        with pytest.raises(RuntimeError, match="busy"), session.connection():
            pytest.fail("admitted verification overlap")
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and errors == []
    assert [call[0] for call in calls] == ["appInfo", "appInfo", "close"]
    assert control.snapshot()["state"] == "idle"


def verification_response(control, *, elapsed):
    if elapsed < 15:
        return "pending"
    return "timeoutBusy" if control.snapshot()["state"] != "idle" else "settled"


def test_vendor_access_restriction_is_not_invalid_key(sdk_rpc):
    application, calls, _, _, failure = sdk_rpc

    class VendorRestriction(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNKNOWN

        def details(self):
            return "Vendor access restriction: subscription or machine policy requires inspection"

    failure.append(VendorRestriction())
    with pytest.raises(RuntimeError) as caught:
        application.Application(api_key_value="synthetic-business-key")
    assert "UNKNOWN" in str(caught.value)
    assert "Vendor access restriction" in str(caught.value)
    assert "invalidKey" not in str(caught.value)
    assert [call[0] for call in calls] == ["appInfo"]


@pytest.mark.parametrize(
    "code", [grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.UNKNOWN]
)
def test_vendor_license_server_network_refusal_is_availability_not_key_or_quota(
    sdk_rpc, code
):
    from dlubal_rfem_mcp.rfem.session import RFEMSession

    _, calls, _, _, failure = sdk_rpc

    class LicenseNetworkFailure(grpc.RpcError):
        def code(self):
            return code

        def details(self):
            return "The Dlubal license server closed the connection unexpectedly. Network error: Connection closed (Error code 2)"

    failure.append(LicenseNetworkFailure())
    session = RFEMSession("synthetic-business-key", "127.0.0.1", 9000)
    with pytest.raises(RuntimeError, match="softwareUnavailable"), session.connection():
        pytest.fail("no engineering dispatch after a license network failure")
    assert not session.verified
    assert session.restriction == "softwareUnavailable"
    assert [call[0] for call in calls] == ["appInfo"]


class StatusFrame(BaseModel):
    """Test-only future stdout projection from OperationControl, not a wire API."""

    model_config = ConfigDict(extra="forbid")
    sequence: int = Field(strict=True, ge=1)
    state: Literal["idle", "running", "unknown", "draining"]


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
    control = OperationControl()
    reader = StatusReader()
    stream = io.StringIO()
    stream.write(json.dumps({"kind": "ready"}) + "\n")
    with control.reading():
        frame = StatusFrame(sequence=2, state=control.snapshot()["state"])
        stream.write(frame.model_dump_json() + "\n")
    lines = stream.getvalue().splitlines()
    assert json.loads(lines[0]) == {"kind": "ready"}
    assert reader.accept(StatusFrame.model_validate_json(lines[1]))
    assert not reader.accept(StatusFrame(sequence=2, state="idle"))
    assert not reader.accept(StatusFrame(sequence=1, state="idle"))
    assert reader.state == "running"
    reader.eof()
    assert reader.state == "unknown"
