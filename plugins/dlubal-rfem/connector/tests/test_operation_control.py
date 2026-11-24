import threading
import time
from uuid import uuid4

import pytest

from dlubal_mcp.core.operation_control import OperationControl
from dlubal_mcp.rfem.execution import ExecutionManager, ExecutionUncertain


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
