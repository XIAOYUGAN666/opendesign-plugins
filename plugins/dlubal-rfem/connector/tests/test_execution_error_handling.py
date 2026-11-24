from uuid import uuid4

import pytest

from dlubal_mcp.core.operation_control import OperationControl
from dlubal_mcp.rfem.execution import ExecutionManager, ExecutionUncertain


class BrokenTextError(Exception):
    def __str__(self):
        raise ValueError("diagnostic formatting failed")


class BrokenUncertainText(ExecutionUncertain):
    def __str__(self):
        raise ValueError("diagnostic formatting failed")


class BrokenCodeProperty(Exception):
    @property
    def code(self):
        raise ValueError("RPC status unavailable")


class BrokenCodeCall(Exception):
    def code(self):
        raise ValueError("RPC status unavailable")


class BrokenCodeText(Exception):
    def code(self):
        return BrokenTextError()


@pytest.mark.parametrize(
    "error_type",
    [
        BrokenTextError,
        BrokenUncertainText,
        BrokenCodeProperty,
        BrokenCodeCall,
        BrokenCodeText,
    ],
)
@pytest.mark.parametrize("product", ["rfem"])
def test_unreadable_error_stays_unknown_and_blocks_replay(
    tmp_path, error_type, product
):
    control = OperationControl()
    path = tmp_path / "state.db"
    manager = ExecutionManager(path, control=control, product=product)
    execution_id = str(uuid4())
    guid = str(uuid4())
    calls = []

    def work(capture, record):
        record(guid)
        record.mutated()
        calls.append("write")
        capture.print("partial output")
        raise error_type()

    result = manager.submit(execution_id, "chat", "code", work, wait_seconds=5)
    assert result["state"] == "unknown"
    assert result["error"]
    assert result["output"] == "partial output\n"
    assert result["model_guid"] == guid
    assert control.prepare_stop()["state"] == "unknown"
    assert manager.submit(execution_id, "chat", "code", work)["state"] == "unknown"
    assert manager.submit(str(uuid4()), "chat", "next", work)["state"] == "busy"
    restored = ExecutionManager(path, product=product)
    assert restored.status(execution_id)["state"] == "unknown"
    assert restored.submit(str(uuid4()), "chat", "next", work)["state"] == "busy"
    assert calls == ["write"]
