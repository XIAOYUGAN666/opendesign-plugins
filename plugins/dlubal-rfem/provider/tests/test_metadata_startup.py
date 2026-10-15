"""Actual V2 composition and SDK boundary, without a licensed RFEM process."""

import asyncio
import io
import json
import socket
import sys

import pytest
from fastmcp import Client
from test_metadata_feasibility import sdk_rpc as sdk_rpc_fixture
from test_origin_boundary import TOOLS

from dlubal_rfem_mcp.core.server import build_mcp
from dlubal_rfem_mcp.managed_bootstrap import BootstrapError, read_envelope
from dlubal_rfem_mcp.rfem.session import RFEMSession

sdk_rpc = sdk_rpc_fixture


def test_configuration_schema_projects_required_key_and_exact_guidance():
    from dlubal_rfem_mcp.core.config import ConfigurationV1

    schema = ConfigurationV1.model_json_schema(by_alias=True)
    assert schema["title"] == "Dlubal RFEM"
    assert (
        schema["description"]
        == "RFEM 插件已安装。使用建模、计算和结果读取功能前，需要配置 Dlubal API Key。"
    )
    assert schema["required"] == ["dlubal-api-key"]
    field = schema["properties"]["dlubal-api-key"]
    assert field["title"] == "Dlubal API Key"
    assert (
        field["description"]
        == "[Dlubal 官方 API Key 获取指南](https://apidocs.dlubal.com/access_control.html)"
    )
    assert field["format"] == "password" and field["writeOnly"] is True


def test_live_harness_uses_typed_v2_without_engineering_software(
    envelope, tmp_path, monkeypatch
):
    import runpy
    from pathlib import Path

    from dlubal_rfem_mcp.core.config import StartupEnvelopeV2

    harness = runpy.run_path(
        str(Path(__file__).parent / "live/test_rfem_complete_contract.py")
    )
    helper = harness.get("_startup_envelope")
    assert callable(helper), "live harness still has inline V1 bootstrap"
    monkeypatch.setenv("DLUBAL_API_KEY", "must-not-import")
    frame = helper(
        {"providerBuildId": envelope["providerBuildId"]},
        envelope["bind"]["port"],
        envelope["bearerToken"],
        envelope["connectorInstanceId"],
        "fixture-manual",
        tmp_path,
        "si_fixture",
    )
    parsed = StartupEnvelopeV2.model_validate(frame)
    assert (
        parsed.mode == "execution"
        and parsed.configuration.api_key.get_secret_value() == "fixture-manual"
    )
    assert parsed.data.stateDirectory == str(tmp_path)


@pytest.mark.asyncio
async def test_live_harness_drains_strict_status_without_software(envelope):
    import runpy
    from pathlib import Path

    from jsonschema import ValidationError

    from dlubal_rfem_mcp.core.config import ProviderLifecycleStatusV1

    harness = runpy.run_path(
        str(Path(__file__).parent / "live/test_rfem_complete_contract.py")
    )
    frame = ProviderLifecycleStatusV1(
        connectorInstanceId=envelope["connectorInstanceId"],
        generation=1,
        sequence=1,
        state="unknown",
        inFlight=0,
    ).model_dump()
    schema = ProviderLifecycleStatusV1.model_json_schema()
    closing = asyncio.Event()
    closing.set()
    reader = asyncio.StreamReader()
    reader.feed_data(
        (
            json.dumps(frame) + "\n" + json.dumps({**frame, "sequence": 2}) + "\n"
        ).encode()
    )
    reader.feed_eof()
    assert await harness["_drain_lifecycle"](reader, envelope, schema, closing) == 2
    for invalid in (
        {**frame, "generation": 2},
        {**frame, "sequence": 3},
        {**frame, "state": "idle", "inFlight": 1},
        {**frame, "secret": "must-not-project"},
    ):
        reader = asyncio.StreamReader()
        reader.feed_data((json.dumps(invalid) + "\n").encode())
        reader.feed_eof()
        with pytest.raises((ValueError, ValidationError)):
            await harness["_drain_lifecycle"](reader, envelope, schema, closing)


def metadata(envelope):
    return {
        **{
            k: v
            for k, v in envelope.items()
            if k not in {"configuration", "data", "software"}
        },
        "schemaVersion": 2,
        "mode": "metadata",
    }


@pytest.mark.asyncio
async def test_build_metadata_declarations_without_business_key_or_rfem(
    envelope, monkeypatch
):
    from dlubal.api.rfem import application

    constructions = []
    monkeypatch.setattr(
        application, "Application", lambda **kw: constructions.append(kw)
    )
    parsed = read_envelope(io.BytesIO(json.dumps(metadata(envelope)).encode()))
    async with Client(build_mcp(parsed)) as client:
        assert {tool.name for tool in await client.list_tools()} == TOOLS
        for tool in sorted(TOOLS):
            result = await client.call_tool(tool, {}, raise_on_error=False)
            assert result.is_error
            assert "credentialMissing" in str(result.content)
    assert constructions == []


def test_engineering_mode_requires_host_key(envelope):
    envelope.update(schemaVersion=2, mode="execution")
    del envelope["configuration"]
    with pytest.raises(BootstrapError):
        read_envelope(io.BytesIO(json.dumps(envelope).encode()))


def test_actual_generic_host_startup_shape(envelope, tmp_path):
    frame = {
        **metadata(envelope),
        "mode": "execution",
        "configuration": {"dlubal-api-key": "fixture-manual"},
        "data": {"stateDirectory": str(tmp_path)},
        "software": envelope["software"],
    }
    parsed = read_envelope(io.BytesIO(json.dumps(frame).encode()))
    assert parsed.mode == "execution"


def test_constructor_failure_is_not_retried(sdk_rpc):
    _, calls, _, _, failure = sdk_rpc
    import grpc

    class Restriction(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAUTHENTICATED

        def details(self):
            return "Access restriction requires inspection"

    failure.append(Restriction())
    with (
        pytest.raises(RuntimeError, match="apiAccessRestricted"),
        RFEMSession("synthetic-business-key", "127.0.0.1", 9000).connection(),
    ):
        pytest.fail("must not dispatch")
    assert [call[0] for call in calls] == ["appInfo"]


@pytest.mark.asyncio
async def test_local_status_requires_real_verification_fact(envelope):
    parsed = read_envelope(io.BytesIO(json.dumps(envelope).encode()))
    async with Client(build_mcp(parsed)) as client:
        result = await client.call_tool(
            "get_rfem_execution", {"execution_id": "unknown"}, raise_on_error=False
        )
        assert result.is_error and "connectionUnverified" in str(result.content)


def test_status_tracks_background_and_unknown(envelope, tmp_path):
    from dlubal_rfem_mcp.core.operation_control import OperationControl
    from dlubal_rfem_mcp.rfem.execution import ExecutionManager

    control = OperationControl()
    manager = ExecutionManager(tmp_path / "journal.sqlite3", control=control)
    with manager._db() as db:
        db.execute(
            "INSERT INTO executions(id,fingerprint,owner,context,state,endpoint,started) VALUES('old','x','dead','','running','',0)"
        )
    frame = control.lifecycle(
        read_envelope(io.BytesIO(json.dumps(envelope).encode())), 1
    )
    assert frame.state == "unknown" and frame.inFlight == 1
    assert set(frame.model_dump()) == {
        "schemaVersion",
        "kind",
        "connectorInstanceId",
        "generation",
        "sequence",
        "state",
        "inFlight",
    }


@pytest.mark.asyncio
async def test_read_only_verify_counts_real_sdk_rpcs_and_local_status_has_no_rpc(
    envelope, sdk_rpc
):
    _, calls, _, _, _ = sdk_rpc
    parsed = read_envelope(io.BytesIO(json.dumps(envelope).encode()))
    async with Client(build_mcp(parsed)) as client:
        result = await client.call_tool("get_rfem_application_info", {})
        assert not result.is_error
        assert [call[0] for call in calls] == ["appInfo", "appInfo", "appInfo", "close"]
        await client.call_tool(
            "get_rfem_execution", {"execution_id": "absent"}, raise_on_error=False
        )
        assert [call[0] for call in calls] == ["appInfo", "appInfo", "appInfo", "close"]


@pytest.mark.asyncio
async def test_verification_response_timeout_keeps_sync_sdk_busy_until_it_finishes(
    envelope, sdk_rpc
):
    from dlubal_rfem_mcp.core.operation_control import OperationControl

    _, calls, entered, release, _ = sdk_rpc
    release.clear()
    control = OperationControl()
    parsed = read_envelope(io.BytesIO(json.dumps(envelope).encode()))
    async with Client(build_mcp(parsed, control=control)) as client:
        task = asyncio.create_task(client.call_tool("get_rfem_application_info", {}))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 0.02)
            assert control.snapshot()["state"] == "running"
            assert "lease" not in control.prepare_stop()
            assert [call[0] for call in calls] == ["appInfo"]
        finally:
            release.set()
            await task
    assert control.snapshot()["state"] == "idle"


@pytest.mark.asyncio
async def test_verification_reports_safe_category_and_blocks_ordinary_retry(
    envelope, sdk_rpc
):
    import grpc

    _, calls, _, _, failure = sdk_rpc

    class Restriction(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.PERMISSION_DENIED

        def details(self):
            return "Private vendor context: permission denied"

    failure.append(Restriction())
    parsed = read_envelope(io.BytesIO(json.dumps(envelope).encode()))
    async with Client(build_mcp(parsed)) as client:
        result = await client.call_tool(
            "get_rfem_application_info", {}, raise_on_error=False
        )
        assert result.is_error and "authorizationRestricted" in str(result.content)
        assert result.structured_content == {
            "status": "error",
            "failureCategory": "authorizationRestricted",
        }
        assert "Private vendor" not in str(result.content)
        retry = await client.call_tool(
            "inspect_rfem_api", {"query": "Material"}, raise_on_error=False
        )
        assert retry.is_error and "authorizationRestricted" in str(retry.content)
        assert [call[0] for call in calls] == ["appInfo"]
        failure.clear()
        verified = await client.call_tool("get_rfem_application_info", {})
        assert not verified.is_error
        assert [call[0] for call in calls] == [
            "appInfo",
            "appInfo",
            "appInfo",
            "appInfo",
            "close",
        ]


@pytest.mark.asyncio
async def test_actual_public_metadata_bootstrap_emits_status_and_denies_all_tools(
    envelope,
):
    frame = metadata(envelope)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        frame["bind"] = {**frame["bind"], "port": sock.getsockname()[1]}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-s",
        "-B",
        "-m",
        "dlubal_rfem_mcp.managed_bootstrap",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        process.stdin.write(json.dumps(frame).encode())
        await process.stdin.drain()
        process.stdin.close()
        ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 20))
        assert ready["mode"] == "metadata" and ready["kind"] == "ready"
        status = json.loads(await asyncio.wait_for(process.stdout.readline(), 2))
        assert (
            status["kind"] == "status"
            and status["state"] == "idle"
            and status["inFlight"] == 0
        )
        async with Client(
            f"http://127.0.0.1:{ready['port']}/mcp", auth=frame["bearerToken"]
        ) as client:
            assert len(await client.list_tools()) == 8
            for name in TOOLS:
                result = await client.call_tool(name, {}, raise_on_error=False)
                assert result.is_error and "credentialMissing" in str(result.content)
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.wait(), 10)


@pytest.mark.asyncio
async def test_standard_client_receives_typed_safe_error(envelope, sdk_rpc):
    import grpc
    import httpx2
    import uvicorn
    from jsonschema import validate
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from dlubal_rfem_mcp.core.server import create_asgi_app

    _, calls, _, _, failure = sdk_rpc

    class Restriction(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.RESOURCE_EXHAUSTED

        def details(self):
            return "quota exceeded; private vendor details"

    failure.append(Restriction())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        envelope["bind"]["port"] = sock.getsockname()[1]
        parsed = read_envelope(io.BytesIO(json.dumps(envelope).encode()))
        server = uvicorn.Server(
            uvicorn.Config(
                create_asgi_app(parsed),
                log_config=None,
                access_log=False,
                log_level="critical",
            )
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            for _ in range(200):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            assert server.started
            async with (
                httpx2.AsyncClient(
                    headers={"Authorization": "Bearer " + envelope["bearerToken"]}
                ) as http,
                streamable_http_client(
                    f"http://127.0.0.1:{envelope['bind']['port']}/mcp", http_client=http
                ) as streams,
                ClientSession(streams[0], streams[1]) as client,
            ):
                await client.initialize()
                page = await client.list_tools()
                schema = next(
                    tool.output_schema
                    for tool in page.tools
                    if tool.name == "get_rfem_application_info"
                )
                result = await client.call_tool("get_rfem_application_info", {})
                assert result.is_error is True
                assert result.structured_content == {
                    "status": "error",
                    "failureCategory": "quotaRestricted",
                }
                validate(result.structured_content, schema)
                assert "private vendor" not in result.model_dump_json()
            assert [call[0] for call in calls] == ["appInfo"]
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)
