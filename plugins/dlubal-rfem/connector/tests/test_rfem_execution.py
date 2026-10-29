import sys
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dlubal_rfem_mcp.rfem.execution import (
    MAX_OUTPUT,
    Capture,
    ExecutionManager,
    ExecutionUncertain,
    worker_lock,
)
from dlubal_rfem_mcp.rfem.model_binding import BoundApplication


def test_timeout_keeps_worker_owned_connection_and_rejects_overlap(tmp_path):
    manager = ExecutionManager(tmp_path / "state.db")
    entered, finish, closed = threading.Event(), threading.Event(), threading.Event()
    calls = []

    @contextmanager
    def connection():
        entered.set()
        try:
            yield
        finally:
            closed.set()

    def work(capture, record):
        with connection():
            calls.append(1)
            capture.print("partial")
            assert finish.wait(5)
            capture.print("complete")

    execution_id = str(uuid4())
    try:
        result = manager.submit(execution_id, "chat", "code", work, wait_seconds=0.01)
        assert entered.wait(1)
        assert result["state"] == "running"
        assert not closed.is_set()
        assert manager.status(execution_id)["output"] == "partial\n"
        assert (
            manager.submit(str(uuid4()), "other-chat", "code", work)["state"] == "busy"
        )
        assert manager.submit(execution_id, "chat", "code", work)["state"] == "running"
        assert (
            manager.submit(execution_id, "chat", "different", work)["state"]
            == "rejected"
        )
    finally:
        finish.set()
    assert closed.wait(1)
    # Join through the manager's completion event without polling/sleeping.
    with manager._lock:
        worker = manager._workers.get(execution_id)
    if worker:
        assert worker[0].wait(1)
    result = manager.submit(execution_id, "chat", "code", work)
    assert result["state"] == "succeeded"
    assert result["output"] == "partial\ncomplete\n"
    assert calls == [1]
    restored = ExecutionManager(tmp_path / "state.db")
    assert restored.submit(execution_id, "chat", "code", work)["state"] == "succeeded"
    assert calls == [1]


def test_restart_or_other_process_cannot_replay_unfinished_work(tmp_path):
    manager = ExecutionManager(tmp_path / "state.db")
    finish = threading.Event()
    execution_id = str(uuid4())
    try:
        assert (
            manager.submit(execution_id, "chat", "code", lambda *_: finish.wait(5), 0)[
                "state"
            ]
            == "running"
        )
        other = ExecutionManager(tmp_path / "state.db")
        assert other.status(execution_id)["state"] == "unknown"
        assert (
            other.submit(
                execution_id, "chat", "code", lambda *_: pytest.fail("replayed")
            )["state"]
            == "unknown"
        )
        assert (
            other.submit(
                str(uuid4()), "chat", "new", lambda *_: pytest.fail("overlap")
            )["state"]
            == "busy"
        )
    finally:
        finish.set()
        with manager._lock:
            worker = manager._workers.get(execution_id)
        if worker:
            assert worker[0].wait(1)


@pytest.mark.parametrize(
    "error", [ExecutionUncertain("cleanup failed"), KeyboardInterrupt()]
)
def test_uncertain_completion_blocks_following_requests(tmp_path, error):
    manager = ExecutionManager(tmp_path / "state.db")

    def work(*_):
        raise error

    result = manager.submit(str(uuid4()), "chat", "code", work)
    assert result["state"] == "unknown"
    assert manager.submit(str(uuid4()), "chat", "next", work)["state"] == "busy"


def test_failure_retains_output_and_completed_model_identity(tmp_path):
    manager = ExecutionManager(tmp_path / "state.db")
    model_a, model_b = str(uuid4()), str(uuid4())

    def work(capture, record):
        record(model_a)
        capture.print("object created")
        raise RuntimeError("next step failed")

    result = manager.submit(str(uuid4()), "chat", "code", work)
    assert result["state"] == "failed"
    assert result["output"] == "object created\n"
    assert manager.binding("chat") == model_a
    manager.bind("chat", model_b)
    assert manager.status(result["execution_id"])["model_guid"] == model_a


@pytest.mark.parametrize("status", ["UNKNOWN", "INVALID_ARGUMENT"])
def test_grpc_failure_never_exposes_raw_debug_details(tmp_path, status):
    manager = ExecutionManager(tmp_path / "state.db")

    class RpcFailure(Exception):
        def code(self):
            return f"StatusCode.{status}"

        def __str__(self):
            return "customer/model.rf6 api_key=secret raw grpc debug"

    result = manager.submit(
        str(uuid4()), "chat", "code", lambda *_: (_ for _ in ()).throw(RpcFailure())
    )

    assert result["state"] == "failed"
    assert (
        result["error"]
        == "RFEM rejected the operation. Inspect RFEM validation messages."
    )
    assert "secret" not in result["error"]
    assert ".rf6" not in result["error"]


def test_capture_is_local_and_bounded(capsys):
    original = sys.stdout
    capture = Capture()
    capture.print("a" * (MAX_OUTPUT + 10))
    print("server log")
    assert sys.stdout is original
    assert capsys.readouterr().out == "server log\n"
    assert capture.text().endswith("[Output truncated]")
    assert len(capture.text()) < MAX_OUTPUT + 100
    with pytest.raises(ValueError):
        capture.print("x", file=sys.stdout)


def test_recovery_cannot_acquire_a_running_workers_os_lock(tmp_path):
    path = tmp_path / "state.db"
    with worker_lock(path), pytest.raises(OSError), worker_lock(path):
        pytest.fail("recovery unlocked a live worker")
    with worker_lock(path):
        pass  # The handle was released after the worker finished.


class ModelId:
    def __init__(self, guid=""):
        self.guid = guid


class FakeApplication:
    def __init__(self):
        self.a, self.b = str(uuid4()), str(uuid4())
        self.active = self.a
        self.models = [self.a, self.b]
        self.calls = []

    def get_model_list(self):
        return SimpleNamespace(model_info=[ModelId(guid) for guid in self.models])

    def get_active_model(self):
        return ModelId(self.active)

    def set_active_model(self, model_id):
        self.active = model_id.guid

    def create_object(self, obj, model_id=None):
        self.calls.append((obj, model_id.guid))

    def get_base_data(self, optional_model_id=None):
        self.calls.append(("base", optional_model_id.guid))

    def future_global_mutation(self):
        pytest.fail("unbound SDK method executed")


def bound(app, guid=None, record=lambda _: None):
    return BoundApplication(
        app, SimpleNamespace(ModelId=ModelId, OptionalModelId=ModelId), guid, record
    )


def test_binding_survives_active_model_changes_and_rejects_foreign_ids():
    app = FakeApplication()
    proxy = bound(app)
    proxy.create_object("first")
    app.active = app.b
    proxy.create_object("second")
    proxy.get_base_data(ModelId())
    assert [guid for _, guid in app.calls] == [app.a] * 3
    assert proxy.get_active_model().guid == app.a
    returned_id = proxy.get_active_model()
    returned_id.guid = app.b
    proxy.create_object("after caller changed its returned ID")
    assert app.calls[-1][1] == app.a
    assert app.active == app.b  # No global UI switch.
    with pytest.raises(RuntimeError, match="different model"):
        proxy.create_object("wrong", ModelId(app.b))
    with pytest.raises(RuntimeError, match="different model"):
        proxy.get_base_data(ModelId(app.b))
    with pytest.raises(RuntimeError, match="Cannot switch"):
        proxy.set_active_model(ModelId(app.b))
    with pytest.raises(RuntimeError, match="no supported model binding"):
        proxy.future_global_mutation()


def test_closed_bound_model_never_falls_back():
    app = FakeApplication()
    proxy = bound(app, app.a)
    app.models.remove(app.a)
    app.active = app.b
    with pytest.raises(RuntimeError, match="no longer open"):
        proxy.create_object("must not reach b")
    assert app.calls == []


def test_another_rfem_endpoint_cannot_silently_rebind_the_chat(tmp_path):
    first = ExecutionManager(tmp_path / "state.db", "127.0.0.1:9000")
    first.bind("chat", str(uuid4()))
    second = ExecutionManager(tmp_path / "state.db", "127.0.0.1:9001")
    with pytest.raises(RuntimeError, match="another RFEM endpoint"):
        second.binding("chat")


def test_session_lock_covers_connection_cleanup(monkeypatch):
    from dlubal_rfem_mcp.rfem.session import RFEMSession

    closing, finish, done = threading.Event(), threading.Event(), threading.Event()

    class App:
        def __init__(self, **kwargs):
            pass

        def close_connection(self):
            closing.set()
            assert finish.wait(5)

    monkeypatch.setitem(
        sys.modules, "dlubal.api.rfem.application", SimpleNamespace(Application=App)
    )
    session = RFEMSession("unused-test-key", "127.0.0.1", 9000)

    def run():
        try:
            with session.connection():
                pass
        finally:
            done.set()

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert closing.wait(1)
        with pytest.raises(RuntimeError, match="busy"), session.connection():
            pytest.fail("cleanup released the session early")
    finally:
        finish.set()
        thread.join(2)
    assert done.is_set()


def test_bound_guid_is_persisted_before_first_model_operation(tmp_path):
    manager = ExecutionManager(tmp_path / "state.db")
    app = FakeApplication()
    bound(app, record=lambda guid: manager.bind("chat", guid)).create_object("first")
    app.active = app.b
    restored = ExecutionManager(tmp_path / "state.db")
    bound(app, restored.binding("chat")).create_object("next")
    assert [guid for _, guid in app.calls] == [app.a, app.a]


@pytest.mark.asyncio
async def test_mcp_wire_flags_validation_failure_and_exposes_execution_status_schema(tmp_path):
    from fastmcp import Client, FastMCP

    from dlubal_rfem_mcp.rfem.tools import register_tools

    mcp = FastMCP("test")
    register_tools(
        mcp, None, SimpleNamespace(execution_state_file=str(tmp_path / "state.db"))
    )
    async with Client(mcp) as client:
        definitions = {tool.name: tool for tool in await client.list_tools()}
        assert {"execution_id", "context_id", "model_guid"} <= definitions[
            "run_rfem_script"
        ].input_schema["properties"].keys()
        result = await client.call_tool(
            "run_rfem_script", {"code": "import os"}, raise_on_error=False
        )
        assert result.is_error
        assert result.structured_content["state"] == "rejected"
        unknown = await client.call_tool(
            "get_rfem_execution", {"execution_id": str(uuid4())}, raise_on_error=False
        )
        # The status lookup succeeded; it does not claim the operation succeeded.
        assert not unknown.is_error
        assert unknown.structured_content["state"] == "not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", ["not_found", "running", "unknown", "failed", "succeeded"]
)
async def test_status_lookup_reports_observed_state_without_mcp_tool_failure(
    tmp_path, monkeypatch, state
):
    from fastmcp import Client, FastMCP

    from dlubal_rfem_mcp.core.product_execution import execution_response
    from dlubal_rfem_mcp.rfem.tools import register_tools

    execution_id = str(uuid4())
    observed = {"execution_id": execution_id, "state": state}
    monkeypatch.setattr(
        ExecutionManager,
        "status",
        lambda self, identity: {**observed, "execution_id": identity},
    )
    mcp = FastMCP("status-only")
    register_tools(
        mcp, None, SimpleNamespace(execution_state_file=str(tmp_path / "state.db"))
    )
    async with Client(mcp) as client:
        status = await client.call_tool(
            "get_rfem_execution", {"execution_id": execution_id}, raise_on_error=False
        )
        assert not status.is_error
        assert status.structured_content == observed
    # Mutation responses must still fail closed unless the engineering work succeeded.
    assert execution_response(observed).to_mcp_result().is_error is (
        state != "succeeded"
    )


@pytest.mark.asyncio
async def test_first_typed_read_binds_following_script_before_ui_switch(
    tmp_path, monkeypatch
):
    from fastmcp import Client, FastMCP

    from dlubal_rfem_mcp.rfem import tools
    from dlubal_rfem_mcp.rfem.results import RFEMResultResponse

    app = FakeApplication()
    common = SimpleNamespace(ModelId=ModelId, OptionalModelId=ModelId)
    monkeypatch.setitem(
        sys.modules,
        "dlubal.api",
        SimpleNamespace(common=common, rfem=SimpleNamespace()),
    )

    class Session:
        @contextmanager
        def connection(self):
            yield app

    def read_results(*, session, **kwargs):
        with session.connection() as reader:
            assert reader.get_active_model().guid == app.a
            return RFEMResultResponse(
                status="no_results", result_type="calculation_status"
            )

    monkeypatch.setattr(tools, "read_results", read_results)
    mcp = FastMCP("test")
    tools.register_tools(
        mcp, Session(), SimpleNamespace(execution_state_file=str(tmp_path / "state.db"))
    )
    async with Client(mcp) as client:
        await client.call_tool(
            "read_rfem_results",
            {"result_type": "calculation_status", "context_id": "chat"},
        )
        app.active = app.b
        result = await client.call_tool(
            "run_rfem_script",
            {"code": "print(app.get_active_model().guid)", "context_id": "chat"},
        )
        assert result.structured_content["model_guid"] == app.a
        assert app.a in result.structured_content["output"]
        assert app.active == app.b


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase,code,state",
    [
        ("connect", "UNAVAILABLE", "failed"),
        ("read", "UNAVAILABLE", "failed"),
        ("write", "UNAVAILABLE", "unknown"),
        ("write", "DEADLINE_EXCEEDED", "unknown"),
        ("write", "UNKNOWN", "failed"),
    ],
)
async def test_only_ambiguous_model_operations_quarantine_the_session(
    tmp_path, monkeypatch, phase, code, state
):
    from fastmcp import Client, FastMCP

    from dlubal_rfem_mcp.rfem.tools import register_tools

    class RpcError(Exception):
        def code(self):
            return "StatusCode." + code

    app = FakeApplication()
    common = SimpleNamespace(ModelId=ModelId, OptionalModelId=ModelId)
    monkeypatch.setitem(
        sys.modules,
        "dlubal.api",
        SimpleNamespace(common=common, rfem=SimpleNamespace()),
    )

    def read():
        raise RpcError("RFEM unavailable")

    def write(obj, model_id=None):
        raise RpcError("RFEM unavailable")

    app.get_application_info = read
    app.create_object = write

    class Session:
        calls = 0

        @contextmanager
        def connection(self):
            self.calls += 1
            if phase == "connect" and self.calls == 1:
                raise RpcError("RFEM starting")
            yield app

    mcp = FastMCP("test")
    register_tools(
        mcp, Session(), SimpleNamespace(execution_state_file=str(tmp_path / "state.db"))
    )
    async with Client(mcp) as client:
        script = (
            "app.create_object('object')"
            if phase == "write"
            else "app.get_application_info()"
        )
        result = await client.call_tool(
            "run_rfem_script", {"code": script}, raise_on_error=False
        )
        assert result.structured_content["state"] == state
        next_call = await client.call_tool(
            "run_rfem_script", {"code": "print(1)"}, raise_on_error=False
        )
        assert next_call.structured_content["state"] == (
            "busy" if state == "unknown" else "succeeded"
        )


@pytest.mark.parametrize("product", ["rfem"])
@pytest.mark.parametrize("failure", ["construct", "start"])
def test_worker_start_failure_retains_recoverable_unknown_status(
    tmp_path, monkeypatch, failure, product
):
    from dlubal_rfem_mcp.core.operation_control import OperationControl

    control = OperationControl()
    manager = ExecutionManager(tmp_path / "state.db", control=control, product=product)
    execution_id = str(uuid4())
    calls = []

    class UnstartableWorker:
        def __init__(self, **kwargs):
            if failure == "construct":
                raise RuntimeError("worker allocation failed")

        def start(self):
            raise RuntimeError("worker start failed")

    with monkeypatch.context() as patch:
        patch.setattr(threading, "Thread", UnstartableWorker)
        result = manager.submit(
            execution_id, "chat", "code", lambda *_: calls.append(1), wait_seconds=0
        )

    assert result["execution_id"] == execution_id
    assert result["state"] == "unknown"
    assert manager.status(execution_id)["state"] == "unknown"
    assert control.prepare_stop()["state"] == "unknown"
    assert control.snapshot()["operations"][0]["state"] == "unknown"
    assert control.snapshot()["operations"][0]["product"] == product
    assert not manager._workers
    assert (
        manager.submit(execution_id, "chat", "code", lambda *_: calls.append(1))[
            "state"
        ]
        == "unknown"
    )
    assert (
        manager.submit(str(uuid4()), "other", "next", lambda *_: calls.append(1))[
            "state"
        ]
        == "busy"
    )
    assert calls == []


@pytest.mark.parametrize("commit_happened", [False, True])
def test_failed_admission_commit_never_leaves_a_phantom_worker(
    tmp_path, monkeypatch, commit_happened
):
    from dlubal_rfem_mcp.core.operation_control import OperationControl

    control = OperationControl()
    manager = ExecutionManager(tmp_path / "state.db", control=control)
    execution_id = str(uuid4())
    original_db = manager._db

    @contextmanager
    def failed_commit():
        with original_db() as db:
            yield db
            if not commit_happened:
                raise OSError("commit failed before persistence")
        raise OSError("commit acknowledgement unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(manager, "_db", failed_commit)
        with pytest.raises(OSError, match="commit"):
            manager.submit(
                execution_id,
                "chat",
                "code",
                lambda *_: pytest.fail("uncommitted work ran"),
            )

    assert not manager._workers
    assert manager.status(execution_id)["state"] == (
        "unknown" if commit_happened else "not_found"
    )
    assert control.snapshot()["state"] == ("unknown" if commit_happened else "idle")
