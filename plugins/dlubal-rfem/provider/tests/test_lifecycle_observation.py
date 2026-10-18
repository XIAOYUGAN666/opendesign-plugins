"""Cold-start observation must prove journal state without engineering access."""

import asyncio
import io
import json
import sqlite3

import pytest
from fastmcp import Client

from dlubal_rfem_mcp.core.config import ProviderReadyV2
from dlubal_rfem_mcp.core.operation_control import OperationControl
from dlubal_rfem_mcp.core.server import build_mcp
from dlubal_rfem_mcp.managed_bootstrap import BootstrapError, read_envelope
from dlubal_rfem_mcp.rfem.execution import ExecutionManager


def observation(envelope):
    return {**envelope, "mode": "observation", "configuration": None, "software": None}


def parse(value):
    return read_envelope(io.BytesIO(json.dumps(value).encode()))


def test_observation_accepts_only_data_and_emits_ready(envelope):
    parsed = parse(observation(envelope))
    ready = ProviderReadyV2(
        mode=parsed.mode,
        connectorInstanceId=parsed.connectorInstanceId,
        generation=parsed.generation,
        providerBuildId=parsed.providerBuildId,
        port=parsed.bind.port,
    )
    assert ready.mode == "observation"
    for key in ("configuration", "software"):
        with pytest.raises(BootstrapError):
            parse({**observation(envelope), key: envelope[key]})
    with pytest.raises(BootstrapError):
        parse({**observation(envelope), "data": None})


@pytest.mark.parametrize("kind", ["missing", "corrupt", "incomplete"])
def test_unprovable_journal_never_creates_idle_or_repairs_schema(
    tmp_path, envelope, kind
):
    path = tmp_path / "absent" / "executions.sqlite3"
    if kind != "missing":
        path.parent.mkdir()
        if kind == "corrupt":
            path.write_bytes(b"not a database")
        else:
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE executions (id TEXT, state TEXT)")
    before = path.read_bytes() if path.exists() else None
    control = OperationControl()
    ExecutionManager(path, control=control, readonly_existing=True)
    assert control.lifecycle(parse(observation(envelope)), 1).state == "unverifiable"
    assert (path.read_bytes() if path.exists() else None) == before
    if kind == "missing":
        assert not path.parent.exists()


@pytest.mark.parametrize(
    "state,want,count",
    [(None, "idle", 0), ("running", "unknown", 1), ("unknown", "unknown", 1)],
)
def test_existing_journal_is_readonly_and_orphans_stay_unknown(
    tmp_path, envelope, state, want, count
):
    path = tmp_path / "executions.sqlite3"
    ExecutionManager(path)
    if state:
        with sqlite3.connect(path) as db:
            db.execute(
                "INSERT INTO executions (id,fingerprint,owner,context,state,endpoint,started) VALUES ('old','x','old-owner','chat',?,'127.0.0.1:9000',1)",
                (state,),
            )
    before = path.read_bytes()
    control = OperationControl()
    manager = ExecutionManager(path, control=control, readonly_existing=True)
    frame = control.lifecycle(parse(observation(envelope)), 1)
    assert (frame.state, frame.inFlight) == (want, count)
    with pytest.raises(PermissionError):
        manager.submit(None, "chat", "x", lambda *_: pytest.fail("engineering ran"))
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_observation_rejects_calls_without_constructing_session(
    envelope, tmp_path, monkeypatch
):
    from test_origin_boundary import TOOLS

    from dlubal_rfem_mcp.core import server

    def forbidden(*args, **kwargs):
        pytest.fail("observation constructed an engineering session")

    monkeypatch.setattr(server, "RFEMSession", forbidden)
    ExecutionManager(tmp_path / "executions.sqlite3")
    control = OperationControl()
    mcp = build_mcp(parse(observation(envelope)), control=control)
    async with Client(mcp) as client:
        assert await client.list_tools() == []
        for name in [*sorted(TOOLS), "anything"]:
            result = await client.call_tool(name, {}, raise_on_error=False)
            assert result.is_error
    assert control.lifecycle(parse(observation(envelope)), 1).state == "idle"


@pytest.mark.asyncio
async def test_observation_and_execution_share_nonblocking_lifetime_lock(
    envelope, tmp_path
):
    from test_provider_ownership import spawn

    ExecutionManager(tmp_path / "executions.sqlite3")
    first = await spawn(observation(envelope))
    second = None
    try:
        ready = await asyncio.wait_for(first.stdout.readline(), 20)
        assert ready, "observation exited before ready"
        assert json.loads(ready)["mode"] == "observation"
        status = json.loads(await asyncio.wait_for(first.stdout.readline(), 10))
        assert (status["state"], status["inFlight"]) == ("idle", 0)
        second = await spawn(envelope)
        assert await asyncio.wait_for(second.stdout.readline(), 20) == b""
        assert await asyncio.wait_for(second.wait(), 10) == 1
    finally:
        for process in (second, first):
            if process is not None:
                if process.returncode is None:
                    process.terminate()
                await asyncio.wait_for(process.wait(), 10)
