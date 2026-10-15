import importlib
import sys
import threading
import time
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
from fastmcp import Client, FastMCP

from dlubal_rfem_mcp.core.operation_control import OperationControl
from dlubal_rfem_mcp.core.product_execution import register_script_tools
from dlubal_rfem_mcp.rfem.execution import ExecutionManager, ExecutionUncertain


def terminal(manager, operation):
    for _ in range(100):
        result = manager.status(operation)
        if result["state"] != "running":
            return result
        time.sleep(0.01)
    pytest.fail("owned worker did not finish")


def test_stop_is_atomic_with_admission_and_requires_explicit_resume(tmp_path):
    control = OperationControl()
    manager = ExecutionManager(tmp_path / "jobs.db", control=control)
    entered, finish = threading.Event(), threading.Event()
    operation = str(uuid4())

    def work(capture, record):
        entered.set()
        assert finish.wait(3)

    try:
        result = manager.submit(operation, "chat", "input", work, wait_seconds=0)
        assert entered.wait(1)
        assert result["state"] == "running"
        assert control.prepare_stop()["state"] == "running"
    finally:
        finish.set()
    assert terminal(manager, operation)["state"] == "succeeded"
    permit = control.prepare_stop()
    assert permit["state"] == "draining" and len(permit["lease"]) == 64
    unsubmitted = str(uuid4())
    with pytest.raises(RuntimeError, match="draining"):
        manager.submit(
            unsubmitted, "chat", "other", lambda *_: pytest.fail("admitted after stop")
        )
    assert manager.status(unsubmitted)["state"] == "not_found"
    with pytest.raises(ValueError):
        control.resume("wrong")
    assert control.snapshot()["state"] == "draining"
    assert control.resume(permit["lease"])["state"] == "idle"
    assert (
        manager.submit(unsubmitted, "chat", "other", lambda *_: None)["state"]
        == "succeeded"
    )


def test_readers_and_orphaned_operations_block_restart(tmp_path):
    control = OperationControl()
    with control.reading():
        assert control.prepare_stop()["state"] == "running"
    original = ExecutionManager(tmp_path / "jobs.db")

    def uncertain(*_):
        raise ExecutionUncertain("connection cleanup failed")

    operation = str(uuid4())
    assert original.submit(operation, "chat", "x", uncertain)["state"] == "unknown"
    ExecutionManager(tmp_path / "jobs.db", control=control, product="rfem")
    result = control.prepare_stop()
    assert result["state"] == "unknown"
    assert result["operations"][0]["product"] == "rfem"
    assert "lease" not in result


@pytest.mark.parametrize("product,cls", [("rfem", "RFEMSession")])
def test_product_session_retains_lock_until_cleanup_finishes(monkeypatch, product, cls):
    closing, finish = threading.Event(), threading.Event()

    class App:
        def __init__(self, **kwargs):
            pass

        def close_connection(self):
            closing.set()
            assert finish.wait(3)

    monkeypatch.setitem(
        sys.modules,
        f"dlubal.api.{product}.application",
        SimpleNamespace(Application=App),
    )
    session = getattr(
        importlib.import_module(f"dlubal_rfem_mcp.{product}.session"), cls
    )("test", "127.0.0.1", 9001)

    def run():
        with session.connection():
            pass

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert closing.wait(1)
        with pytest.raises(RuntimeError, match="busy"), session.connection():
            pytest.fail("cleanup released the lock")
    finally:
        finish.set()
        worker.join(2)
    assert not worker.is_alive()


@pytest.mark.asyncio
@pytest.mark.parametrize("product", ["rfem"])
async def test_product_tools_persist_results_without_global_stdout(
    monkeypatch, tmp_path, product
):
    api = ModuleType("dlubal.api")
    api.common = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "dlubal.api", api)
    monkeypatch.setitem(sys.modules, f"dlubal.api.{product}", ModuleType(product))

    class Session:
        @contextmanager
        def connection(self):
            yield SimpleNamespace()

    config = SimpleNamespace(
        execution_state_file=str(tmp_path / "jobs.db"), url="127.0.0.1", api_port=9001
    )
    original_stdout = sys.stdout
    mcp = FastMCP("product-lifecycle")
    control = OperationControl()
    names = register_script_tools(mcp, Session(), config, product, control)
    assert f"get_{product}_execution" in names
    operation = str(uuid4())
    async with Client(mcp) as client:
        first = await client.call_tool(
            f"run_{product}_script",
            {"code": "print('bounded output')", "execution_id": operation},
        )
        assert first.structured_content["state"] == "succeeded"
        assert first.structured_content["output"] == "bounded output\n"
        status = await client.call_tool(
            f"get_{product}_execution", {"execution_id": operation}
        )
        assert status.structured_content["output"] == first.structured_content["output"]
        conflict = await client.call_tool(
            f"run_{product}_script",
            {"code": "print('changed')", "execution_id": operation},
            raise_on_error=False,
        )
        assert conflict.is_error and conflict.structured_content["state"] == "rejected"
    assert sys.stdout is original_stdout
    assert control.prepare_stop()["state"] == "draining"
